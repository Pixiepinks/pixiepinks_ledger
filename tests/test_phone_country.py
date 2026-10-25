"""Country metadata only: no geocoding, customer mutation or external lookup."""
import phonenumbers
import pytest
from phone_country_service import phone_country


@pytest.mark.parametrize('number,region,name,code,flag',[
    ('+94712345678','LK','Sri Lanka','+94','🇱🇰'),
    ('+447400123456','GB','United Kingdom','+44','🇬🇧'),
    ('+971501234567','AE','United Arab Emirates','+971','🇦🇪'),
    ('+61412345678','AU','Australia','+61','🇦🇺'),
    ('+919876543210','IN','India','+91','🇮🇳'),
    ('+12025550123','US','United States','+1','🇺🇸'),
    ('+14165550123','CA','Canada','+1','🇨🇦'),
])
def test_supported_international_country(number,region,name,code,flag):
    assert phone_country(number)=={'region':region,'name':name,'dialing_code':code,'flag':flag}


@pytest.mark.parametrize('region',['BS','JM','PR','DO','IM','GG','JE','NZ','DE','ZA','JP'])
def test_shared_codes_and_other_international_regions(region):
    example=phonenumbers.example_number(region)
    number=phonenumbers.format_number(example,phonenumbers.PhoneNumberFormat.E164)
    result=phone_country(number)
    assert result and result['region']==region
    assert result['dialing_code']=='+'+str(example.country_code)


@pytest.mark.parametrize('raw',['94712345678','+94 71 234 5678','0094712345678','  +94 (71) 234-5678  '])
def test_meta_digit_storage_and_international_formats(raw):
    assert phone_country(raw)['region']=='LK'


@pytest.mark.parametrize('raw',[None,'',False,94712345678,{},[], '0712345678','+94','+1','+99912345678',
    '12345','+18005551234','+18885551234','+80012345678','+882162345678','+94712345678 ext 123',
    '+94712345678<script>','tel:+94712345678','+94+712345678','+'+'1'*16,'1'*10000,'+９４７１２３４５６７８'])
def test_invalid_missing_ambiguous_non_geographic_and_unsupported(raw):
    assert phone_country(raw) is None
