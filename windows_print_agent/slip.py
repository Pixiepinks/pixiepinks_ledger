"""Constant non-financial control slip for the isolated driver-only drawer queue."""
def drawer_slip(jid):
    from fpdf import FPDF
    pdf=FPDF(unit='mm',format=(80,50));pdf.set_margins(5,5,5);pdf.set_auto_page_break(False);pdf.add_page();pdf.set_font('Helvetica',size=9)
    pdf.multi_cell(0,5,'PixiePinks controlled drawer action\nNOT A SALES RECEIPT\n'+jid)
    return bytes(pdf.output())
