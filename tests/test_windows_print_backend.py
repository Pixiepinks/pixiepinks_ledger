"""Exercise actual Windows backend logic with a mocked pywin32/PDFium boundary."""
import sys
from types import SimpleNamespace
import pytest
from windows_print_agent.windows import WindowsPrinters
from windows_print_agent.transport import AgentError
from windows_print_agent.protocol import RECEIPT_QUEUE,LABEL_QUEUE


def info():
    dm=SimpleNamespace(DriverData=b'vendor drawer-none and cutter settings',Fields=0,PaperSize=256,PaperWidth=800,PaperLength=500,Orientation=1,Copies=1)
    return {'pDevMode':dm,'pPrinterName':RECEIPT_QUEUE,'pDriverName':'POS-80','pPortName':'USB001','pPrintProcessor':'winprint','pDatatype':'RAW','Attributes':0,'Status':0,'cJobs':0}


def test_fingerprint_covers_vendor_private_settings():
    row=info();original=WindowsPrinters.fingerprint_info(row)
    row['pDevMode'].DriverData=b'cashbox enabled'
    assert WindowsPrinters.fingerprint_info(row)!=original


def test_status_offline_spooler_and_usb_unavailable():
    b=object.__new__(WindowsPrinters)
    b.print=SimpleNamespace(PRINTER_STATUS_OFFLINE=1,PRINTER_ATTRIBUTE_WORK_OFFLINE=2,PRINTER_STATUS_ERROR=4,PRINTER_STATUS_PAPER_OUT=8,PRINTER_STATUS_PAPER_JAM=16,PRINTER_STATUS_DOOR_OPEN=32,PRINTER_STATUS_PAUSED=64,PRINTER_STATUS_NOT_AVAILABLE=128,PRINTER_STATUS_USER_INTERVENTION=256)
    row=info();b.info=lambda name:row
    assert b.status([RECEIPT_QUEUE])[0]['state']=='READY'
    row['Status']=1;assert b.status([RECEIPT_QUEUE])[0]['state']=='OFFLINE'
    row['Status']=8;assert b.status([RECEIPT_QUEUE])[0]['state']=='ERROR'
    b.info=lambda name:(_ for _ in ()).throw(OSError('USB unbound/spooler unavailable'))
    assert b.status([RECEIPT_QUEUE])[0]['state']=='UNAVAILABLE'


def fake_backend(monkeypatch):
    calls=[];row=info();b=object.__new__(WindowsPrinters);b.info=lambda queue:row
    b.con=SimpleNamespace(DM_PAPERSIZE=1,DM_PAPERWIDTH=2,DM_PAPERLENGTH=4,DM_ORIENTATION=8,DM_COPIES=16)
    caps={88:203,90:203,110:639,111:400,112:0,113:0}
    class DC:
        def GetDeviceCaps(self,key):return caps[key]
        def StartDoc(self,name):calls.append(('StartDoc',name));return 42
        def StartPage(self):calls.append(('StartPage',))
        def GetHandleOutput(self):return 7
        def EndPage(self):calls.append(('EndPage',))
        def EndDoc(self):calls.append(('EndDoc',))
        def DeleteDC(self):calls.append(('DeleteDC',))
        def AbortDoc(self):calls.append(('AbortDoc',))
    b.gui=SimpleNamespace(CreateDC=lambda driver,queue,dm:calls.append(('CreateDC',queue,dm.PaperWidth,dm.PaperLength,dm.DriverData)) or 1)
    b.ui=SimpleNamespace(CreateDCFromHandle=lambda handle:DC())
    class Image:
        width=640;height=400
        def convert(self,mode):return self
        def close(self):pass
    class Bitmap:
        def to_pil(self):return Image()
        def close(self):pass
    class Page:
        def get_size(self):return 80*72/25.4,50*72/25.4
        def render(self,scale):calls.append(('render',scale));return Bitmap()
        def close(self):pass
    class Document:
        def __init__(self,raw):pass
        def __len__(self):return 1
        def __getitem__(self,index):return Page()
        def close(self):pass
    class Dib:
        def __init__(self,image):pass
        def draw(self,handle,rectangle):calls.append(('draw',rectangle))
    monkeypatch.setitem(sys.modules,'pypdfium2',SimpleNamespace(PdfDocument=Document))
    monkeypatch.setitem(sys.modules,'PIL.ImageWin',SimpleNamespace(Dib=Dib))
    return b,row,caps,calls


def test_gdi_uses_private_driver_profile_and_no_global_preference_writes(monkeypatch):
    b,row,caps,calls=fake_backend(monkeypatch)
    assert b.submit(RECEIPT_QUEUE,b'%PDF-','test-id','RECEIPT',b.fingerprint_info(row))==42
    assert ('CreateDC',RECEIPT_QUEUE,800,500,b'vendor drawer-none and cutter settings') in calls
    assert ('draw',(0,0,639,400)) in calls
    assert len([v for v in calls if v[0]=='StartDoc'])==1
    # No SetPrinter, WritePrinter, raw kick bytes or shell execution exist at this boundary.


def test_geometry_mismatch_blocks_before_startdoc(monkeypatch):
    b,row,caps,calls=fake_backend(monkeypatch);caps[110]=300
    with pytest.raises(AgentError):b.submit(RECEIPT_QUEUE,b'%PDF-','test-id','RECEIPT',b.fingerprint_info(row))
    assert not any(v[0]=='StartDoc' for v in calls)


def test_changed_driver_profile_blocks_before_startdoc(monkeypatch):
    b,row,caps,calls=fake_backend(monkeypatch);fingerprint=b.fingerprint_info(row);row['pDevMode'].DriverData=b'drawer changed'
    with pytest.raises(AgentError):b.submit(RECEIPT_QUEUE,b'%PDF-','test-id','RECEIPT',fingerprint)
    assert not any(v[0]=='StartDoc' for v in calls)


def test_label_pixels_are_never_fit_scaled(monkeypatch):
    b,row,caps,calls=fake_backend(monkeypatch)
    b.submit(LABEL_QUEUE,b'%PDF-','label-id','LABEL',b.fingerprint_info(row))
    assert ('draw',(0,0,640,400)) in calls  # Raster's exact pixels, not resized to 639.


def test_effective_per_user_driver_mode_is_used():
    b=object.__new__(WindowsPrinters);global_info=info();effective=SimpleNamespace(DriverData=b'agent-user private mode')
    b.print=SimpleNamespace(OpenPrinter=lambda name:1,GetPrinter=lambda handle,level:{'pDevMode':effective} if level==9 else global_info,ClosePrinter=lambda handle:None)
    assert b.info(RECEIPT_QUEUE)['pDevMode'] is effective


def test_shared_device_rejects_network_or_file_port():
    b=object.__new__(WindowsPrinters);b.print=SimpleNamespace(PRINTER_ATTRIBUTE_NETWORK=16);row=info();b.info=lambda name:row
    assert b.shares_port([RECEIPT_QUEUE,LABEL_QUEUE])
    row['pPortName']='FILE:';assert not b.shares_port([RECEIPT_QUEUE,LABEL_QUEUE])
    row['pPortName']='USB001';row['Attributes']=16;assert not b.shares_port([RECEIPT_QUEUE,LABEL_QUEUE])


def test_drawer_requires_direct_printing_without_retention():
    b=object.__new__(WindowsPrinters);b.print=SimpleNamespace(PRINTER_ATTRIBUTE_DIRECT=2,PRINTER_ATTRIBUTE_KEEPPRINTEDJOBS=256);row=info();b.info=lambda name:row
    assert not b.drawer_safe('control')
    row['Attributes']=2;assert b.drawer_safe('control')
    row['Attributes']=2|256;assert not b.drawer_safe('control')
