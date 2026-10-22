"""Disabled-by-default Meta API boundary. It performs no requests and stores no tokens.

The future adapter must implement official Instagram Login OAuth, server-side secret
storage, permission/account checks, private-media URL creation, container state
reconciliation, quota checks and supported insights. Staging stays disabled until the
Meta app/account is independently verified and live activation is explicitly allowed.
"""
from typing import Protocol

class MetaUnavailable(RuntimeError): pass

class ServerTokenStore(Protocol):
    def load(self, account_id: str) -> str: ...
    def save(self, account_id: str, token: str, expires_at: str) -> None: ...
    def delete(self, account_id: str) -> None: ...

class MetaClient:
    REQUIRED_FEATURE_FLAG='META_LIVE_PUBLISH_ENABLED'
    def __init__(self,token_store:ServerTokenStore|None=None,live_enabled:bool=False):
        self.token_store=token_store;self.live_enabled=live_enabled
    def _disabled(self):
        raise MetaUnavailable('Meta integration is not implemented or enabled; no network calls made')
    def begin_oauth(self,*args,**kwargs):return self._disabled()
    def exchange_oauth_code(self,*args,**kwargs):return self._disabled()
    def refresh_token(self,*args,**kwargs):return self._disabled()
    def account_eligibility(self,*args,**kwargs):return self._disabled()
    def account_health(self,*args,**kwargs):return self._disabled()
    def granted_permissions(self,*args,**kwargs):return self._disabled()
    def prepare_media(self,*args,**kwargs):return self._disabled()
    def create_media_container(self,*args,**kwargs):return self._disabled()
    def container_status(self,*args,**kwargs):return self._disabled()
    def publish_container(self,*args,**kwargs):return self._disabled()
    def reconcile_publish(self,*args,**kwargs):return self._disabled()
    def publishing_quota(self,*args,**kwargs):return self._disabled()
    def retrieve_insights(self,*args,**kwargs):return self._disabled()
