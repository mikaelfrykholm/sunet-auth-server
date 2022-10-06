# generated by datamodel-codegen:
#   filename:  https://raw.githubusercontent.com/dotse/tls-fed-auth/master/tls-fed-metadata.yaml
#   timestamp: 2021-05-04T15:28:02+00:00

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import AnyUrl, BaseModel, Extra, Field, conint, constr

from auth_server.models.jose import JOSEHeader


class TLSFEDJOSEHeader(JOSEHeader):
    iat: datetime
    exp: datetime
    iss: Optional[str]


class RegisteredExtensions(str, Enum):
    SAML_SCOPE = "https://kontosynk.internetstiftelsen.se/saml-scope"


class SAMLScopeExtension(BaseModel):
    scope: List[str]


class Extensions(BaseModel):
    class Config:
        extra = Extra.allow
        allow_population_by_field_name = True  # allow registered extension to also be set by name, not only by alias

    saml_scope: Optional[SAMLScopeExtension] = Field(default=None, alias=RegisteredExtensions.SAML_SCOPE.value)


class CertIssuers(BaseModel):
    x509certificate: Optional[str] = Field(None, title="X.509 Certificate (PEM)")


class Alg(str, Enum):
    sha256 = "sha256"


class PinDirective(BaseModel):
    alg: Alg = Field(..., example="sha256", title="Directive name")
    digest: constr(regex=r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$") = Field(  # type: ignore
        ...,
        example="HiMkrb4phPSP+OvGqmZd6sGvy7AUn4k3XEe8OMBrzt8=",
        title="Directive value (Base64)",
    )


class Endpoint(BaseModel):
    class Config:
        extra = Extra.allow

    description: Optional[str] = Field(None, example="SCIM Server 1", title="Endpoint description")
    tags: Optional[List[constr(regex=r"^[a-z0-9]{1,64}$")]] = Field(  # type: ignore
        None,
        description="A list of strings that describe the endpoint's capabilities.\n",
        title="Endpoint tags",
    )
    base_uri: Optional[AnyUrl] = Field(None, example="https://scim.example.com", title="Endpoint base URI")
    pins: List[PinDirective] = Field(..., title="Certificate pin set")


class Entity(BaseModel):
    class Config:
        extra = Extra.allow

    entity_id: AnyUrl = Field(
        ...,
        description="Globally unique identifier for the entity.",
        example="https://example.com",
        title="Entity identifier",
    )
    organization: Optional[str] = Field(
        None,
        description="Name identifying the organization that the entity’s\nmetadata represents.\n",
        example="Example Org",
        title="Name of entity organization",
    )
    issuers: List[CertIssuers] = Field(
        ...,
        description="A list of certificate issuers that are allowed to issue certificates\nfor the entity's endpoints. For each issuer, the issuer's root CA\ncertificate is included in the x509certificate property (PEM-encoded).\n",
        title="Entity certificate issuers",
    )
    servers: Optional[List[Endpoint]] = None
    clients: Optional[List[Endpoint]] = None
    # added after generation
    organization_id: Optional[str] = None
    extensions: Optional[Extensions] = None


class Model(BaseModel):
    class Config:
        extra = Extra.allow

    version: constr(regex=r"^\d+\.\d+\.\d+$") = Field(..., example="1.0.0", title="Metadata schema version")  # type: ignore
    cache_ttl: Optional[conint(ge=0)] = Field(  # type: ignore
        None,
        description="How long (in seconds) to cache metadata.\nEffective maximum TTL is the minimum of HTTP Expire and TTL\n",
        example=3600,
        title="Metadata cache TTL",
    )
    entities: List[Entity]
