"""Administrator-only staff lifecycle and section checkboxes."""
import hmac
import re
from datetime import timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,Form,HTTPException,Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError,SQLAlchemyError
from models import User,StaffAccessAudit
from utils_auth import hash_password
from staff_access import SECTIONS,KEYS


def build_router(get_db,require_user,csrf,templates):
    router=APIRouter()

    def guard(request,user,token=None):
        if user.role!='ADMIN':raise HTTPException(403,'Administrator access required')
        if token is not None:
            expected=request.session.get('csrf_token','')
            if not expected or not hmac.compare_digest(expected,token):raise HTTPException(403,'Invalid CSRF token')

    def grants(sections):
        if any(key not in KEYS for key in sections):raise HTTPException(422,'Unknown access section')
        return sorted(set(sections))

    def password(value):
        if '\x00' in value or len(value)<12 or len(value.encode('utf-8'))>72:
            raise HTTPException(422,'Password must contain at least 12 characters and at most 72 UTF-8 bytes')
        return hash_password(value)

    def render(request,user,db,error=None,status=200):
        accounts=db.query(User).order_by(User.username).all()
        return templates.TemplateResponse('staff_management.html',{
            'request':request,'user':user,'page_title':'Staff & Access','sections':SECTIONS,
            'staff':accounts,'account_names':{account.id:account.username for account in accounts},
            'audit':db.query(StaffAccessAudit).order_by(StaffAccessAudit.id.desc()).limit(50).all(),
            'csrf':csrf(request),'error':error,
            'local_time':lambda dt:dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Colombo')).strftime('%d %b %Y, %I:%M %p')},status_code=status,headers={'Cache-Control':'private, no-store'})

    @router.get('/admin/staff')
    def index(request:Request,db=Depends(get_db),user=Depends(require_user)):
        guard(request,user);return render(request,user,db)

    @router.post('/admin/staff')
    def create(request:Request,username:str=Form(...),new_password:str=Form(...),sections:list[str]=Form([]),
               csrf_token:str=Form(...),db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        try:
            name=username.strip().lower()
            if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,99}',name):raise HTTPException(422,'Username must be 3–100 letters, numbers, dots, underscores or hyphens')
            access=grants(sections);hashed=password(new_password)
            if db.query(User).filter(func.lower(User.username)==name).first():raise HTTPException(409,'Username already exists')
            target=User(username=name,password_hash=hashed,role='STAFF',section_access=access,is_active=True,session_version=0)
            db.add(target);db.flush()
            db.add(StaffAccessAudit(actor_id=user.id,target_id=target.id,action='CREATED',changes={'sections':access,'active':True}))
            db.commit()
        except HTTPException as exc:
            db.rollback();return render(request,user,db,exc.detail,exc.status_code)
        except IntegrityError:
            db.rollback();return render(request,user,db,'Username already exists',409)
        except SQLAlchemyError:
            db.rollback();raise HTTPException(503,'Staff account could not be saved. No changes committed.') from None
        return RedirectResponse('/admin/staff',status_code=303)

    @router.post('/admin/staff/{staff_id}')
    def update(staff_id:int,request:Request,sections:list[str]=Form([]),active:bool=Form(False),
               new_password:str=Form(''),expected_version:int=Form(...),csrf_token:str=Form(...),db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        target=db.query(User).filter_by(id=staff_id).with_for_update().one_or_none()
        if not target:raise HTTPException(404,'Staff account not found')
        if target.role=='ADMIN' or target.id==user.id:raise HTTPException(403,'Administrator accounts cannot be changed here')
        if target.session_version!=expected_version:raise HTTPException(409,'This account changed. Refresh before saving access.')
        try:
            access=grants(sections);hashed=password(new_password) if new_password else None
            changes={'previous_sections':target.section_access,'sections':access,
                     'previous_active':target.is_active,'active':active,'password_reset':hashed is not None}
            values={'section_access':access,'is_active':active,'session_version':expected_version+1}
            if hashed:values['password_hash']=hashed
            changed=db.query(User).filter_by(id=target.id,session_version=expected_version).update(values,synchronize_session=False)
            if not changed:raise HTTPException(409,'This account changed. Refresh before saving access.')
            db.add(StaffAccessAudit(actor_id=user.id,target_id=target.id,action='UPDATED',changes=changes));db.commit()
        except HTTPException as exc:
            db.rollback();return render(request,user,db,exc.detail,exc.status_code)
        except SQLAlchemyError:
            db.rollback();raise HTTPException(503,'Staff access could not be saved. No changes committed.') from None
        return RedirectResponse('/admin/staff',status_code=303)

    @router.get('/staff-home')
    def home(request:Request,user=Depends(require_user)):
        return templates.TemplateResponse('staff_home.html',{'request':request,'user':user,'page_title':'Your workspace'},headers={'Cache-Control':'private, no-store'})
    return router
