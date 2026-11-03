"""Opt-in, task-owned PostgreSQL migration and concurrent-admin regression."""
import os
import re
from uuid import uuid4
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
import database
import main
from models import Base,User,StaffAccessAudit
from utils_auth import hash_password


def test_postgres_migrates_legacy_users_and_fences_simultaneous_admin_updates(monkeypatch):
    url=os.getenv('STAFF_ACCESS_TEST_DATABASE_URL','')
    if not url:pytest.skip('Task-owned PostgreSQL URL not configured')
    parsed=urlsplit(url)
    assert parsed.hostname in {'localhost','127.0.0.1'} and parsed.path=='/staff_access_test'
    admin=create_engine(url);schema='staff_access_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    previous=dict(main.app.dependency_overrides)
    try:
        password=hash_password('Staff-Test-Password-2026')
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE users (id SERIAL PRIMARY KEY,username VARCHAR(100) NOT NULL UNIQUE,password_hash VARCHAR(255) NOT NULL,role VARCHAR(30) NOT NULL)'))
            conn.execute(text("INSERT INTO users (username,password_hash,role) VALUES ('pg-admin',:hash,'ADMIN')"),{'hash':password})
        monkeypatch.setattr(database,'engine',engine)
        config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        command.stamp(config,'20261030_21');command.upgrade(config,'20261103_22');command.upgrade(config,'20261103_22')
        with engine.connect() as conn:
            row=conn.execute(text('SELECT section_access,is_active,session_version,password_hash FROM users')).one()
            assert row==(None,True,0,password)
        Base.metadata.create_all(engine)
        sessions=sessionmaker(bind=engine)
        with sessions() as db:
            target=User(username='pg-staff',password_hash=password,role='STAFF',section_access=['whatsapp'])
            db.add(target);db.commit();target_id=target.id
        def get_db():
            with sessions() as db:yield db
        main.app.dependency_overrides[main.get_db]=get_db
        clients=[]
        for _ in range(2):
            client=TestClient(main.app,base_url='https://testserver')
            client.post('/login',data={'username':'pg-admin','password':'Staff-Test-Password-2026'},follow_redirects=False)
            page=client.get('/admin/staff')
            token=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
            clients.append((client,token))
        def update(index):
            client,token=clients[index]
            return client.post('/admin/staff/'+str(target_id),data={'sections':['customers' if index else 'whatsapp'],
                'active':'true','expected_version':0,'csrf_token':token},follow_redirects=False).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(update,range(2)))
        assert sorted(statuses)==[303,409]
        with sessions() as db:
            assert db.get(User,target_id).session_version==1
            assert db.query(StaffAccessAudit).count()==1
    finally:
        main.app.dependency_overrides.clear();main.app.dependency_overrides.update(previous)
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
