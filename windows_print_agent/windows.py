"""Windows GDI + PDFium only. No raw USB bytes, shell commands or remote scripts."""
import hashlib,json,os
from contextlib import contextmanager
from .protocol import *
from .transport import AgentError

class WindowsPrinters:
    def __init__(self):
        if os.name!='nt':raise AgentError('Windows runtime required')
        import win32print,win32ui,win32gui,win32con,win32event,win32api
        self.print=win32print;self.ui=win32ui;self.gui=win32gui;self.con=win32con;self.event=win32event;self.api=win32api
    @contextmanager
    def exclusive(self):
        # Both DBL queues (including drawer control) share one physical resource.
        handle=self.event.CreateMutex(None,False,'Global\\PixiePinks.DBL.PhysicalPrinter')
        state=self.event.WaitForSingleObject(handle,0)
        if state not in {self.event.WAIT_OBJECT_0,self.event.WAIT_ABANDONED}:self.api.CloseHandle(handle);raise AgentError('Another local agent owns the printer')
        try:yield
        finally:self.event.ReleaseMutex(handle);self.api.CloseHandle(handle)
    def info(self,name):
        handle=self.print.OpenPrinter(name)
        try:
            info=self.print.GetPrinter(handle,2)
            # Printing preferences can be per-user. Attest and use the effective agent-user mode.
            try:effective=self.print.GetPrinter(handle,9).get('pDevMode')
            except Exception:effective=None
            if effective is not None:info['pDevMode']=effective
            return info
        finally:self.print.ClosePrinter(handle)
    def fingerprint(self,name):
        return self.fingerprint_info(self.info(name))
    @staticmethod
    def fingerprint_info(info):
        dm=info['pDevMode']
        if dm is None:raise AgentError('Driver mode unavailable')
        private=dm.DriverData or b''
        if not isinstance(private,bytes):raise AgentError('Complete private driver settings unavailable')
        values={k:info.get(k) for k in ['pPrinterName','pDriverName','pPortName','pPrintProcessor','pDatatype']}
        values['devmode']={k:getattr(dm,k,None) for k in ['DeviceName','SpecVersion','DriverVersion','Size','DriverExtra','Fields','Orientation','PaperSize','PaperLength','PaperWidth','Copies','PrintQuality','YResolution','Duplex','FormName']}
        values['transport_attributes']=int(info.get('Attributes',0)) & (2|16|256)
        values['private_sha256']=hashlib.sha256(private).hexdigest()
        return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
    def enumerate(self):return [row[2] for row in self.print.EnumPrinters(self.print.PRINTER_ENUM_LOCAL|self.print.PRINTER_ENUM_CONNECTIONS,None,1)]
    def status(self,names):
        rows=[]
        for name in names:
            try:
                info=self.info(name);status=info['Status'];attrs=info['Attributes']
                offline=bool(status & self.print.PRINTER_STATUS_OFFLINE or attrs & self.print.PRINTER_ATTRIBUTE_WORK_OFFLINE)
                bad=self.print.PRINTER_STATUS_ERROR|self.print.PRINTER_STATUS_PAPER_OUT|self.print.PRINTER_STATUS_PAPER_JAM|self.print.PRINTER_STATUS_DOOR_OPEN|self.print.PRINTER_STATUS_PAUSED|self.print.PRINTER_STATUS_NOT_AVAILABLE|self.print.PRINTER_STATUS_USER_INTERVENTION
                rows.append({'name':name,'state':'OFFLINE' if offline else 'ERROR' if status & bad else 'READY','jobs':int(info['cJobs'])})
            except Exception:rows.append({'name':name,'state':'UNAVAILABLE','jobs':0})
        return rows
    def shares_port(self,names):
        infos=[self.info(name) for name in names]
        return len({i['pPortName'] for i in infos})==1 and all(str(i['pPortName']).upper().startswith(('USB','DOT4')) and not i['Attributes'] & self.print.PRINTER_ATTRIBUTE_NETWORK for i in infos)
    def drawer_safe(self,name):
        info=self.info(name);attributes=info['Attributes']
        return bool(attributes & self.print.PRINTER_ATTRIBUTE_DIRECT) and not bool(attributes & self.print.PRINTER_ATTRIBUTE_KEEPPRINTEDJOBS)
    @staticmethod
    def validate_pdf(raw,media):
        import pypdfium2 as pdfium
        document=pdfium.PdfDocument(raw)
        try:
            if not 1<=len(document)<=MAX_PAGES:raise AgentError('Page count outside limit')
            for index in range(len(document)):
                page=document[index]
                try:
                    w,h=page.get_size();wm,hm=w*25.4/72,h*25.4/72
                    if media=='A4':ok=abs(wm-210)<1 and abs(hm-297)<1
                    elif media=='RECEIPT':ok=min(abs(wm-58),abs(wm-80))<1 and 20<=hm<=1500
                    else:ok=20<=wm<=210.5 and 12<=hm<=297.5
                    if not ok:raise AgentError('PDF does not match approved physical media')
                finally:page.close()
        finally:document.close()
    def submit(self,queue,raw,jid,media,expected_fingerprint,progress=None):
        import pypdfium2 as pdfium
        from PIL import ImageWin
        self.validate_pdf(raw,media)
        doc=pdfium.PdfDocument(raw);dc=None;started=False;jobid=None
        try:
            for index in range(len(doc)):
                page=doc[index];bitmap=None;image=None
                try:
                    if progress:progress()
                    width,height=page.get_size();wm,hm=width*25.4/72,height*25.4/72
                    info=self.info(queue)
                    if self.fingerprint_info(info)!=expected_fingerprint:raise AgentError('Driver settings changed; reattest locally')
                    dm=info['pDevMode']
                    if dm is None:raise AgentError('Driver configuration unavailable')
                    # Only this DC receives public paper settings; never SetPrinter/global preferences.
                    dm.PaperSize=256;dm.PaperWidth=round(wm*10);dm.PaperLength=round(hm*10);dm.Orientation=1;dm.Copies=1
                    dm.Fields|=self.con.DM_PAPERSIZE|self.con.DM_PAPERWIDTH|self.con.DM_PAPERLENGTH|self.con.DM_ORIENTATION|self.con.DM_COPIES
                    if dc is not None:raise AgentError('Mixed or repeated DC setup')
                    hdc=self.gui.CreateDC('WINSPOOL',queue,dm);dc=self.ui.CreateDCFromHandle(hdc)
                    dpi_x=dc.GetDeviceCaps(88);dpi_y=dc.GetDeviceCaps(90)
                    if dpi_x!=dpi_y or not 203<=dpi_x<=1200:raise AgentError('Unsupported printer resolution')
                    physical_w=dc.GetDeviceCaps(110);physical_h=dc.GetDeviceCaps(111);offset_x=dc.GetDeviceCaps(112);offset_y=dc.GetDeviceCaps(113)
                    expected_w=round(wm*dpi_x/25.4);expected_h=round(hm*dpi_x/25.4)
                    if abs(physical_w-expected_w)>max(3,dpi_x*.5/25.4) or abs(physical_h-expected_h)>max(3,dpi_x*.5/25.4):raise AgentError('Driver paper geometry mismatch; do not fit/scale labels')
                    if media=='LABEL' and (offset_x>dpi_x*.5/25.4 or offset_y>dpi_x*.5/25.4):raise AgentError('Driver margins exceed label safety allowance')
                    render_dpi=dpi_x if media=='LABEL' else min(dpi_x,300);scale=render_dpi/72
                    if width*height*scale*scale>50000000:raise AgentError('Raster memory budget exceeded')
                    bitmap=page.render(scale=scale);image=bitmap.to_pil().convert('RGB')
                    # A new DC per page retains exact PDF page geometry and cutter-per-page behavior.
                    # StartDoc occurs once per physical page; all pages are one durable agent action.
                    jobid=dc.StartDoc('PixiePinks '+jid);started=True;dc.StartPage()
                    target_w,target_h=(image.width,image.height) if media=='LABEL' else (expected_w,expected_h)
                    ImageWin.Dib(image).draw(dc.GetHandleOutput(),(-offset_x,-offset_y,target_w-offset_x,target_h-offset_y))
                    dc.EndPage();dc.EndDoc();started=False;dc.DeleteDC();dc=None
                finally:
                    if image is not None:image.close()
                    if bitmap is not None:bitmap.close()
                    page.close()
            return int(jobid)
        except Exception:
            if dc and started:
                try:dc.AbortDoc()
                except Exception:pass
            raise
        finally:
            if dc:dc.DeleteDC()
            doc.close()
