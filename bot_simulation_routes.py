"""Manager-only general simulator. No record-writing or live-gate overrides."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from bot_simulation_service import SimulationInput, SimulationError, simulate
from learning_routes import authorize, read_body


class SimulatorRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def safe(request):
            try: response = await original(request)
            except SQLAlchemyError:
                response = JSONResponse({"detail": "Simulator database read failed. No preview was saved."}, status_code=503)
            except HTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)
            response.headers.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
            return response
        return safe


def build_router(get_db, require_user, require_csrf, needs_catalog):
    router = APIRouter(prefix="/crm/bot-management/api", route_class=SimulatorRoute)
    @router.post("/simulate")
    async def run(request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user, "manage"); require_csrf(request)
        body = await read_body(request)
        try: values = SimulationInput.model_validate(body)
        except ValidationError:
            raise HTTPException(422, "Choose DRAFT/LIVE, a bounded synthetic question/context and an optional valid Shopify handle only") from None
        try: return await run_in_threadpool(simulate, db, user.id, values, needs_catalog)
        except SimulationError as error: raise HTTPException(error.status_code, str(error)) from None
    return router
