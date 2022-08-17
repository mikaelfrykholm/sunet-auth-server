# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import pprint
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from logging import getLogger
from typing import Any, Dict, List, NewType, Optional, Tuple, Union
from xml.etree.ElementTree import ParseError

from pydantic import AnyUrl, BaseModel, Extra, Field
from pymongo import MongoClient
from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT
from saml2.cache import Cache
from saml2.client import Saml2Client
from saml2.config import SPConfig
from saml2.response import AuthnResponse, StatusError, UnsolicitedResponse
from saml2.saml import Subject

from auth_server.config import load_config
from auth_server.db.client import get_mongo_client
from auth_server.db.mongo_cache import MongoCache

__author__ = 'lundberg'

logger = getLogger(__name__)

AuthnRequestRef = NewType('AuthnRequestRef', str)


class BadSAMLResponse(Exception):
    pass


@dataclass
class SAML2SP:
    client: Saml2Client
    outstanding_queries_cache: OutstandingQueriesCache
    authn_req_cache: AuthenticationRequestCache
    discovery_service_url: Optional[AnyUrl]
    authn_sign_alg: str = 'http://www.w3.org/2001/04/xmldsig-more#rsa-sha256'
    authn_digest_alg: str = 'http://www.w3.org/2001/04/xmlenc#sha256'


class AuthnInfo(BaseModel):
    authn_class: str
    authn_authority: List[str] = Field(default_factory=list)
    authn_instant: datetime


class NameID(BaseModel):
    format: str
    name_qualifier: Optional[str]
    sp_name_qualifier: Optional[str]
    sp_provided_id: Optional[str]
    id: str


class SAMLAttributes(BaseModel):
    assurance: List[str] = Field(default_factory=list, alias='eduPersonAssurance')
    common_name: Optional[str] = Field(default=None, alias='cn')
    country_code: Optional[str] = Field(default=None, alias='c')
    country_name: Optional[str] = Field(default=None, alias='co')
    date_of_birth: Optional[str] = Field(default=None, alias='schacDateOfBirth')
    display_name: Optional[str] = Field(default=None, alias='displayName')
    eppn: Optional[str] = Field(default=None, alias='eduPersonPrincipalName')
    given_name: Optional[str] = Field(default=None, alias='givenName')
    home_organization: Optional[str] = Field(default=None, alias='schacHomeOrganization')
    home_organization_type: Optional[str] = Field(default=None, alias='schacHomeOrganizationType')
    mail: Optional[str]
    nin: Optional[str] = Field(default=None, alias='norEduPersonNIN')
    organization_acronym: Optional[str] = Field(default=None, alias='norEduOrgAcronym')
    organization_name: Optional[str] = Field(default=None, alias='o')
    personal_identity_number: Optional[str] = Field(default=None, alias='personalIdentityNumber')
    scoped_affiliation: Optional[str] = Field(default=None, alias='eduPersonScopedAffiliation')
    surname: Optional[str] = Field(default=None, alias='sn')
    targeted_id: Optional[str] = Field(default=None, alias='eduPersonTargetedID')
    unique_id: Optional[str] = Field(default=None, alias='eduPersonUniqueId')

    class Config:
        extra = Extra.allow  # allow unspecified attributes
        allow_population_by_field_name = True

    @classmethod
    def from_pysaml2(cls, ava: Dict[str, List[str]]) -> SAMLAttributes:
        # pysaml returns attributes in lists, lets unwind all attributes with only a single value
        for key, value in ava.items():
            if not isinstance(value, list):
                raise ValueError('attribute value is not a list')
        single_values = dict([(key, value[0]) for key, value in ava.items() if len(value) == 1])
        # please mypy
        result: Dict[str, Union[str, List[str]]] = {}
        result.update(ava)
        result.update(single_values)
        return cls(**result)


class SessionInfo(BaseModel):
    issuer: str
    authn_info: List[AuthnInfo] = Field(default_factory=list)
    name_id: NameID
    attributes: SAMLAttributes

    @classmethod
    def from_pysaml2(cls, session_info: Dict[str, Any]) -> SessionInfo:
        session_info['authn_info'] = [
            AuthnInfo(authn_class=item[0], authn_authority=item[1], authn_instant=item[2])
            for item in session_info['authn_info']
        ]
        session_info['name_id'] = NameID(
            format=session_info['name_id'].format,
            name_qualifier=session_info['name_id'].name_qualifier,
            sp_name_qualifier=session_info['name_id'].sp_name_qualifier,
            sp_provided_id=session_info['name_id'].sp_provided_id,
            id=session_info['name_id'].text,
        )
        session_info['attributes'] = SAMLAttributes.from_pysaml2(ava=session_info['ava'])
        return cls(**session_info)


class AssertionData(BaseModel):
    session_info: SessionInfo
    authn_req_ref: AuthnRequestRef


class OutstandingQueriesCache(MongoCache):
    """
    Handles the queries that has been sent to an IdP that has not replied yet.
    """

    def __init__(
        self,
        db_client: MongoClient,
        db_name: str = 'auth_server',
        collection: str = 'pysaml2_outstanding_queries',
        expire_after: timedelta = timedelta(hours=1),
    ):
        super().__init__(db_client=db_client, db_name=db_name, collection=collection, expire_after=expire_after)

    def get(self, saml2_session_id, default: Optional[Any] = None) -> Optional[Any]:
        if saml2_session_id in self:
            return self[saml2_session_id]
        return default

    def set(self, saml2_session_id, came_from) -> None:
        self[saml2_session_id] = came_from

    def delete(self, saml2_session_id) -> None:
        if saml2_session_id in self:
            del self[saml2_session_id]


class IdentityCache(Cache):
    """
    Handles information about the users that have been successfully logged in.

    This information is useful because when the user logs out we must
    know where does he come from in order to notify such IdP/AA.
    """

    def __init__(
        self,
        db_client: MongoClient,
        db_name: str = 'auth_server',
        collection: str = 'pysaml2_identity_cache',
        expire_after: timedelta = timedelta(days=10),
    ):
        super().__init__()  # just please pycharm as we set self._db again below
        self._db = MongoCache(db_client=db_client, db_name=db_name, collection=collection, expire_after=expire_after)


class StateCache(MongoCache):
    """
    Store state information that is needed to associate a logout request with its response.
    """

    def __init__(
        self,
        db_client: MongoClient,
        db_name: str = 'auth_server',
        collection: str = 'pysaml2_state_cache',
        expire_after: timedelta = timedelta(days=10),
    ):
        super().__init__(db_client=db_client, db_name=db_name, collection=collection, expire_after=expire_after)


class AuthenticationRequestCache(MongoCache):
    def __init__(
        self,
        db_client: MongoClient,
        db_name: str = 'auth_server',
        collection: str = 'saml_authentications_cache',
        expire_after: timedelta = timedelta(days=10),
    ):
        super().__init__(db_client=db_client, db_name=db_name, collection=collection, expire_after=expire_after)


async def get_saml2_sp() -> Optional[SAML2SP]:
    config = load_config()
    if not config.pysaml2_config_path or not config.mongo_uri:
        return None
    sp_config = get_pysaml2_sp_config(name=config.pysaml2_config_name)

    mongo_client = await get_mongo_client()
    if not mongo_client:
        return None

    state_cache = StateCache(db_client=mongo_client)
    identity_cache = IdentityCache(db_client=mongo_client)
    return SAML2SP(
        client=Saml2Client(sp_config, state_cache=state_cache, identity_cache=identity_cache),
        outstanding_queries_cache=OutstandingQueriesCache(db_client=mongo_client),
        authn_req_cache=AuthenticationRequestCache(db_client=mongo_client),
        discovery_service_url=config.saml2_discovery_service_url,
    )


@lru_cache
def get_pysaml2_sp_config(name) -> SPConfig:
    """
    Load SAML2 config file, in the form of a Python module
    """
    config = load_config()
    spec = importlib.util.spec_from_file_location('saml2_settings', config.pysaml2_config_path)
    if spec is None:
        raise RuntimeError(f'Failed loading saml2_settings module: {config.pysaml2_config_path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore

    conf = SPConfig()
    conf.load(module.__getattribute__(name))
    return conf


async def get_redirect_url(http_info):
    """Extract the redirect URL from a pysaml2 http_info object"""
    assert 'headers' in http_info
    headers = http_info['headers']

    assert len(headers) == 1
    header_name, header_value = headers[0]
    assert header_name == 'Location'
    return header_value


async def get_authn_request(
    relay_state: str,
    authn_id: AuthnRequestRef,
    selected_idp: Optional[str],
    force_authn: bool = False,
    sign_alg: Optional[str] = None,
    digest_alg: Optional[str] = None,
    subject: Optional[Subject] = None,
):
    kwargs = {
        'force_authn': str(force_authn).lower(),
    }
    logger.debug(f'Authn request args: {kwargs}')

    saml2_sp = await get_saml2_sp()
    if saml2_sp is None:
        return None

    try:
        (session_id, info) = saml2_sp.client.prepare_for_authenticate(
            entityid=selected_idp,
            relay_state=relay_state,
            binding=BINDING_HTTP_REDIRECT,
            sigalg=sign_alg,
            digest_alg=digest_alg,
            subject=subject,
            **kwargs,
        )
    except TypeError:
        logger.error('Unable to know which IdP to use')
        raise

    saml2_sp.outstanding_queries_cache.set(session_id, authn_id)
    return info


async def process_assertion(saml_response: str) -> Optional[AssertionData]:
    """
    Code to process a received SAML assertion.
    """
    saml2_sp = await get_saml2_sp()
    if saml2_sp is None:
        return None

    response, authn_ref = await get_authn_response(saml_response)
    logger.debug(f'authn response: {response}')

    if authn_ref not in saml2_sp.authn_req_cache:
        logger.info(f'Unknown response')
        raise BadSAMLResponse('Unknown response')

    session_info = SessionInfo.from_pysaml2(response.session_info())
    assertion_data = AssertionData(session_info=session_info, authn_req_ref=authn_ref)
    # Fix eduPersonTargetedID
    issuer_entityid = assertion_data.session_info.issuer
    sp_entityid = saml2_sp.client.config.entityid
    targeted_id_value = assertion_data.session_info.attributes.targeted_id
    assertion_data.session_info.attributes.targeted_id = f'{issuer_entityid}!{sp_entityid}!{targeted_id_value}'
    return assertion_data


async def get_authn_response(raw_response: str) -> Tuple[AuthnResponse, AuthnRequestRef]:
    """
    Check a SAML response and return the response.

    The response can be used to retrieve a session_info dict.

    Example session_info:

    {'authn_info': [('urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport', [],
                     '2019-06-17T00:00:01Z')],
     'ava': {'eduPersonPrincipalName': ['eppn@eduid.se'],
             'eduidIdPCredentialsUsed': ['...']},
     'came_from': 'https://dashboard.eduid.se/profile/personaldata',
     'issuer': 'https://login.idp.eduid.se/idp.xml',
     'name_id': <saml2.saml.NameID object>,
     'not_on_or_after': 156000000,
     'session_index': 'id-foo'}
    """
    saml2_sp = await get_saml2_sp()
    if saml2_sp is None:
        raise RuntimeError('SAML SP not configured, this should have been caught earlier')

    try:
        # process the authentication response
        response = saml2_sp.client.parse_authn_request_response(
            raw_response, BINDING_HTTP_POST, saml2_sp.outstanding_queries_cache
        )
    except AssertionError:
        logger.error('SAML response is not verified')
        raise BadSAMLResponse('SAML response is not verified')
    except ParseError as e:
        logger.error(f'SAML response is not correctly formatted: {repr(e)}')
        raise BadSAMLResponse('SAML response is not correctly formatted')
    except UnsolicitedResponse:
        logger.error('Unsolicited SAML response')
        # Extra debug to try and find the cause for some of these that seem to be incorrect
        logger.debug(f'Outstanding queries cache: {saml2_sp.outstanding_queries_cache}')
        logger.debug(f'Outstanding queries: {saml2_sp.outstanding_queries_cache.items()}')
        raise BadSAMLResponse('Unsolicited SAML response')
    except StatusError as e:
        logger.error(f'SAML response was a failure: {repr(e)}')
        raise BadSAMLResponse('SAML status error')

    if response is None:
        logger.error('SAML response is None')
        raise BadSAMLResponse('No SAML authn response')

    session_id = response.session_id()
    authn_reqref = saml2_sp.outstanding_queries_cache.get(session_id)
    assert authn_reqref is not None  # please mypy
    authn_reqref = AuthnRequestRef(authn_reqref)
    saml2_sp.outstanding_queries_cache.delete(session_id)

    logger.debug(
        f'Response {session_id}, request reference {authn_reqref}\n'
        f'session info:\n{pprint.pformat(response.session_info())}\n\n'
    )

    return response, authn_reqref