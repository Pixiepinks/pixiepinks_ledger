"""ACL planning and approval mocks only; no Windows security descriptor is set."""
import hashlib,json,os,shutil,subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]


def ps(*args):
    executable=os.getenv('TEST_PWSH') or shutil.which('pwsh') or '/tmp/pixiepinks-pwsh/pwsh'
    if not Path(executable).exists():pytest.skip('Set TEST_PWSH to PowerShell 7 for isolated Windows ACL mocks')
    env={**os.environ,'XDG_CACHE_HOME':'/tmp/pixiepinks-pwsh-cache','XDG_CONFIG_HOME':'/tmp/pixiepinks-pwsh-config','XDG_DATA_HOME':'/tmp/pixiepinks-pwsh-data'}
    return subprocess.run([executable,'-NoProfile',*map(str,args)],capture_output=True,text=True,env=env,timeout=60)


def test_plans_inheritance_approval_rollback_and_stronger_guard():
    result=ps('-File',ROOT/'tests/windows/test_receipt_acl_repair.ps1')
    assert result.returncode==0,result.stdout+result.stderr
    assert 'ACL_REPAIR_MOCK_TESTS_PASSED' in result.stdout and 'ACL_INSTALLER_GUARD_TESTS_PASSED' in result.stdout
    count=int(result.stdout.split('ACL_ASSERTIONS=')[-1].strip())
    assert count>=40


@pytest.mark.parametrize('mode,expected',[('Plan','P24A_WRONG_WINDOWS_TARGET'),('Apply','P24A_REVIEWED_PLAN_REQUIRED'),('Rollback','P24A_REVIEWED_PLAN_REQUIRED')])
def test_command_defaults_fail_closed_without_approved_windows_context(mode,expected):
    if os.name=='nt' and mode=='Plan':pytest.skip('Native production enumeration is intentionally not invoked by this test')
    result=ps('-File',ROOT/'scripts/windows/repair_receipt_startup_acl.ps1','-Mode',mode)
    assert result.returncode==1
    data=json.loads(result.stdout)
    assert data['error_category']==expected and data['status']=='STOPPED_NO_TASK_ACTION'
    assert data['acl_mutation_requested']==(mode!='Plan')


def test_export_is_create_new_and_never_overwrites(tmp_path):
    target=tmp_path/'before.json';helper=ROOT/'scripts/windows/receipt_acl_repair.ps1'
    # Only fixture JSON I/O; native Windows adapters are not called.
    source=f". '{helper}';Write-ReceiptNewJson '{target}' ([pscustomobject]@{{original=1}});try{{Write-ReceiptNewJson '{target}' ([pscustomobject]@{{original=2}});exit 9}}catch{{exit 0}}"
    result=ps('-Command',source)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(target.read_text())=={'original':1}


@pytest.mark.parametrize('correct',[True,False])
def test_backup_read_uses_verified_byte_snapshot(tmp_path,correct):
    target=tmp_path/'backup.json'
    body={'format':1,'inventory':{'install':r'C:\PixiePinks\ReceiptConnector-190','device_id':'f18b74ce-cf1f-440f-973f-579640ff46e6'}}
    target.write_text(json.dumps(body),encoding='utf8');digest=hashlib.sha256(target.read_bytes()).hexdigest() if correct else '0'*64
    helper=ROOT/'scripts/windows/receipt_acl_repair.ps1'
    source=f". '{helper}';try{{$b=Read-ReceiptAclBackup '{target}' '{digest}';Write-Output 'VERIFIED';exit 0}}catch{{Write-Output $_.Exception.Message;exit 1}}"
    result=ps('-Command',source)
    assert result.returncode==(0 if correct else 1)
    assert ('VERIFIED' if correct else 'P24A_BACKUP_HASH_MISMATCH') in result.stdout
    assert json.loads(target.read_text())==body


def test_dpapi_bridge_compiles_and_rejects_only_fixture_ciphertext(tmp_path):
    # Native DTO parsing keeps decrypted JSON out of PowerShell cmdlet parameters.
    sentinel='DO_NOT_LOG_TOKEN_FIXTURE_ONLY'
    (tmp_path/'credentials.dpapi').write_bytes(sentinel.encode())
    helper=ROOT/'scripts/windows/receipt_acl_repair.ps1'
    source=f". '{helper}';try{{Test-ReceiptDeviceBinding '{tmp_path}';exit 9}}catch{{if($_.Exception.Message -eq 'P24A_DEVICE_BINDING_MISMATCH'){{Write-Output 'REJECTED_SAFELY';exit 0}};exit 1}}"
    result=ps('-Command',source)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'REJECTED_SAFELY' in result.stdout and sentinel not in result.stdout+result.stderr



def test_hardlinked_file_cannot_be_repaired_through_another_application(tmp_path):
    first=tmp_path/'shared.py';second=tmp_path/'outside-link.py'
    first.write_text('fixture only');os.link(first,second)
    helper=ROOT/'scripts/windows/receipt_acl_repair.ps1'
    source=f". '{helper}';try{{Read-ReceiptAcl '{first}' $true $false;exit 9}}catch{{Write-Output $_.Exception.Message;if($_.Exception.Message -eq 'P24A_HARDLINK_REQUIRES_REVIEW'){{exit 0}};exit 1}}"
    result=ps('-Command',source)
    assert result.returncode==0,result.stdout+result.stderr
    assert first.read_text()==second.read_text()=='fixture only'
