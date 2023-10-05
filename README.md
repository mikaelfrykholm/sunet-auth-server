# sunet-auth-server

oauth.xyz/GNAP auth server

sunet-auth-server a service handing out JSON Web Tokens via the GNAP protocol
and is based on FastAPI. It can use federated SAML2 or federated TLS as authentication
sources. For SAML2 it uses a MongoDB as state storage.

Please see the included [config_mtls.example.yaml](config_mtls.example.yaml) or [config_saml2.example.yaml](config_saml2.example.yaml) for examples on how to configure the service.
The service can be run standalone for testing and should be behind a proxy in production.

This implementation targets verstion 10 of [draft-ietf-gnap-core-protocol](https://datatracker.ietf.org/doc/draft-ietf-gnap-core-protocol/).

For API documentation, in the docs diretctory, run `make html`.
