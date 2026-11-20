"""Controlled Railway entrypoint, default off; requires exclusive network isolation.

A forwarding header is not cryptographic proof. This entrypoint MUST NOT be
approved on a shared private network. See docs/print-agent-railway-ingress.md.
"""
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from settings import settings
from print_agent_security import isolated_ingress_scope,_INGRESS_SCOPE_KEY


class RailwayIsolatedIngress:
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']=='http':
            scope=dict(scope)
            scope.pop(_INGRESS_SCOPE_KEY,None)
            path=scope.get('path','')
            native=path=='/api/print-agent' or path.startswith('/api/print-agent/')
            if native and settings.PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED:
                try:scope=isolated_ingress_scope(scope)
                except HTTPException:
                    response=JSONResponse({'detail':'Native agent over TLS required'},status_code=403,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
                    await response(scope,receive,send);return
        # No rewriting, buffering, credential handling, logging or forwarding to
        # a second HTTP port. All existing CRM and lifespan behavior is retained.
        await self.app(scope,receive,send)


from main import app as crm_app
app=RailwayIsolatedIngress(crm_app)
