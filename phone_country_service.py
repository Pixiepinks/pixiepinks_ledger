"""Offline phone numbering-plan region labels, never customer location inference."""
import re

import phonenumbers
from babel import Locale

_ENGLISH = Locale.parse('en')


def phone_country(value):
    """Return public region metadata or None; require an international number.

    Meta stores international digits without '+'. No default country is supplied
    and no local-number normalization, city lookup or external service is used.
    """
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        return None
    value = value.strip()
    if not re.fullmatch(r'\+?[0-9][0-9 ().-]*', value):
        return None
    digits = re.sub(r'[^0-9]', '', value)
    if not value.startswith('+') and digits.startswith('00'):
        digits = digits[2:]
    if not 1 <= len(digits) <= 15:
        return None
    try:
        number = phonenumbers.parse('+' + digits, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    region = phonenumbers.region_code_for_number(number)
    if number.country_code == 1:
        # Shared NANP services (e.g. toll-free) may match many regions; the
        # library's primary-region fallback must not label them United States.
        candidates = [candidate for candidate in phonenumbers.region_codes_for_country_code(1)
                      if phonenumbers.is_valid_number_for_region(number, candidate)]
        if len(candidates) != 1:
            return None
        region = candidates[0]
    if not isinstance(region, str) or not re.fullmatch(r'[A-Z]{2}', region):
        return None  # Includes non-geographic +800/+882 '001' regions.
    name = _ENGLISH.territories.get(region)
    if not name:
        return None
    return {'region': region, 'name': name, 'dialing_code': f'+{number.country_code}',
            'flag': ''.join(chr(0x1F1E6 + ord(letter) - ord('A')) for letter in region)}
