# -*- coding: utf-8 -*-

from typing import Callable, Optional, Union

from fastapi import Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel

from auth_server.models.gnap import GNAPJOSEHeader

__author__ = 'lundberg'


class Context(BaseModel):
    key_reference: Optional[str] = None
    jws_verified: bool = False
    jws_header: Optional[GNAPJOSEHeader] = None

    def to_dict(self):
        return self.dict()


class ContextRequest(Request):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def context(self):
        try:
            return self.state.context
        except AttributeError:
            # Lazy init of self.state.context
            self.state.context = Context()
            return self.context

    @context.setter
    def context(self, context: Context):
        self.state.context = context


class ContextRequestMixin:
    @staticmethod
    def make_context_request(request: Union[Request, ContextRequest]) -> ContextRequest:
        if not isinstance(request, ContextRequest):
            request = ContextRequest(request.scope, request.receive)
        return request


class ContextRequestRoute(APIRoute, ContextRequestMixin):
    """
    Make ContextRequest the default request class
    """

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def context_route_handler(request: Union[Request, ContextRequest]) -> Response:
            request = self.make_context_request(request)
            return await original_route_handler(request)

        return context_route_handler
