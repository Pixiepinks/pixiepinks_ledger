"""Exact LKR pricing and deterministic per-unit discount/tax allocation."""
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP,ROUND_DOWN
from fastapi import HTTPException
P=Decimal('0.01')


def money(value,maximum=Decimal('999999999.99')):
    try:
        d=Decimal(str(value))
        if not d.is_finite() or d<0 or d>maximum or d!=d.quantize(P):raise ValueError()
        return d.quantize(P)
    except (InvalidOperation,ValueError,TypeError):raise HTTPException(422,'Use a nonnegative LKR amount with at most two decimals') from None


def fmt(value):return format(value.quantize(P,rounding=ROUND_HALF_UP),'.2f')


def discount(gross,rule):
    if not isinstance(rule,dict) or set(rule)!={'type','value'} or rule['type'] not in {'FIXED','PERCENT'}:raise HTTPException(422,'Discount must specify FIXED/PERCENT and value')
    value=money(rule['value'])
    if rule['type']=='PERCENT':
        if value>100:raise HTTPException(422,'Percentage cannot exceed 100')
        value=(gross*value/100).quantize(P,rounding=ROUND_HALF_UP)
    if value>gross:raise HTTPException(422,'Discount exceeds the amount due')
    return value


def allocate(total,weights):
    if not weights:return []
    cents=int(total*100);den=sum(weights)
    if not den:return [0]*len(weights)
    raw=[Decimal(cents)*w/den for w in weights];result=[int(v.to_integral_value(rounding=ROUND_DOWN)) for v in raw]
    for i in sorted(range(len(raw)),key=lambda i:(-(raw[i]-result[i]),i))[:cents-sum(result)]:result[i]+=1
    return result


def calculate(lines,order_discount,tax_mode,tax_rate):
    if not isinstance(lines,list) or len(lines)>20:raise HTTPException(422,'Cart supports at most 20 variants')
    rows=[]
    for line in lines:
        qty=line['quantity']
        if type(qty) is not int or not 1<=qty<=99:raise HTTPException(422,'Quantity must be 1–99')
        price=money(line['unit_price']);gross=price*qty;cut=discount(gross,line['discount'])
        rows.append({**line,'gross':gross,'line_discount':cut,'net':gross-cut})
    subtotal=sum((r['gross'] for r in rows),Decimal('0'));line_cuts=sum((r['line_discount'] for r in rows),Decimal('0'))
    order_cut=discount(subtotal-line_cuts,order_discount);shares=allocate(order_cut,[r['net'] for r in rows])
    tax_total=Decimal('0');remote=[];snapshots=[]
    for index,r in enumerate(rows):
        net=r['net']-Decimal(shares[index])/100
        tax=(net*tax_rate/100).quantize(P,rounding=ROUND_HALF_UP) if tax_mode=='EXCLUSIVE' and r['taxable'] else Decimal('0')
        tax_total+=tax
        # Per-unit allocation is immutable: partial refunds cannot exceed original cents.
        units=allocate(net,[Decimal(1)]*r['quantity']);tax_units=allocate(tax,[Decimal(1)]*r['quantity'])
        groups={}
        for a,b in zip(units,tax_units):groups[(a,b)]=groups.get((a,b),0)+1
        segments=[]
        for segment,((unit,tax_unit),qty) in enumerate(groups.items()):
            part={'line':index,'segment':segment,'variant_id':r['variant_id'],'quantity':qty,'net_unit':fmt(Decimal(unit)/100),
                'tax_unit':fmt(Decimal(tax_unit)/100),'gross_unit':r['unit_price'],'taxable':r['taxable']}
            remote.append(part);segments.append(part)
        snapshots.append({**{k:v for k,v in r.items() if k not in {'gross','line_discount','net'}},'gross':fmt(r['gross']),
            'line_discount':fmt(r['line_discount']),'order_discount':fmt(Decimal(shares[index])/100),'net':fmt(net),'tax':fmt(tax),'total':fmt(net+tax),'segments':segments})
    total=subtotal-line_cuts-order_cut+tax_total
    return {'lines':snapshots,'segments':remote,'subtotal':fmt(subtotal),'discount_total':fmt(line_cuts+order_cut),
        'order_discount':fmt(order_cut),'tax_total':fmt(tax_total) if tax_mode!='UNCONFIGURED' else None,
        'grand_total':fmt(total) if tax_mode!='UNCONFIGURED' else None,'currency':'LKR','tax_confirmed':tax_mode!='UNCONFIGURED'}
