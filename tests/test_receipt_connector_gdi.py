"""Windows GDI APIs mocked; validates silent actual-size raster submission only."""
import copy
from types import SimpleNamespace
import pytest
from fpdf import FPDF
from PIL import ImageWin
from windows_print_agent.windows import WindowsPrinters
from windows_print_agent.transport import AgentError


def printer(monkeypatch,clipped=False):
    backend=object.__new__(WindowsPrinters);calls=[]
    mode=SimpleNamespace(DriverData=b'vendor-private-settings',Fields=0)
    info={'pPrinterName':'POSPrinter POS-80','pDriverName':'existing-driver','pPortName':'USB001','pPrintProcessor':'winprint','pDatatype':'EMF','Attributes':0,'pDevMode':mode}
    backend.info=lambda queue:copy.deepcopy(info)
    caps={88:203,90:203,110:639,111:480,112:48 if clipped else 16,113:16,8:543 if clipped else 607,10:448}
    class DC:
        def GetDeviceCaps(self,key):return caps[key]
        def StartDoc(self,title):calls.append(('StartDoc',title));return 77
        def StartPage(self):calls.append(('StartPage',))
        def EndPage(self):calls.append(('EndPage',))
        def EndDoc(self):calls.append(('EndDoc',))
        def DeleteDC(self):pass
        def AbortDoc(self):calls.append(('AbortDoc',))
        def GetHandleOutput(self):return 'mock-hdc'
    backend.gui=SimpleNamespace(CreateDC=lambda *args:'mock-hdc')
    backend.ui=SimpleNamespace(CreateDCFromHandle=lambda handle:DC())
    backend.con=SimpleNamespace(**{key:1<<index for index,key in enumerate(['DM_PAPERSIZE','DM_PAPERWIDTH','DM_PAPERLENGTH','DM_ORIENTATION','DM_COPIES'])})
    class Dib:
        def __init__(self,image):self.image=image
        def draw(self,handle,rectangle):calls.append(('draw',rectangle))
    monkeypatch.setattr(ImageWin,'Dib',Dib)
    return backend,calls,info


def sample():
    pdf=FPDF(unit='mm',format=(80,60));pdf.set_auto_page_break(False);pdf.add_page();pdf.set_font('Helvetica',size=10);pdf.text(5,10,'SYNTHETIC TEST - NO SALE')
    return bytes(pdf.output())


def test_silent_gdi_receipt_uses_actual_pdf_physical_dimensions(monkeypatch):
    backend,calls,info=printer(monkeypatch);fingerprint=backend.fingerprint_info(info)
    assert backend.submit('POSPrinter POS-80',sample(),'synthetic-job','RECEIPT',fingerprint)==77
    assert [row[0] for row in calls]==['StartDoc','StartPage','draw','EndPage','EndDoc']
    assert next(row[1] for row in calls if row[0]=='draw')==(-16,-16,623,464)
    assert info['pDevMode'].Fields==0 # Only a copy/DC mode changed, never global queue.


def test_inadequate_driver_printable_region_rejected_before_spool(monkeypatch):
    backend,calls,info=printer(monkeypatch,True)
    with pytest.raises(AgentError,match='printable region'):backend.submit('POSPrinter POS-80',sample(),'synthetic','RECEIPT',backend.fingerprint_info(info))
    assert not any(row[0]=='StartDoc' for row in calls)
