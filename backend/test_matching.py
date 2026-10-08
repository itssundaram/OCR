import re

def normalize(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower())

lines = [
    "receipt issue and expense voucher",
    "iv no : iv/mt/veh/09",
    "unit : 15 corps engg sig regt",
    "stn : c/o 56 apo",
    "issued to: det rvp (ncvd) c/o 56 apo",
    "chassis no : 759393f",
    "veh ba no : 08a 060276a",
]

fields = {
    "IV NO": "1V/MT/Veh/09",
    "UNIT FROM": "15 Corps Engg Sig Regt",
    "UNIT TO": "C/O 56 APO"
}

for fname, fval in fields.items():
    norm_field = normalize(fval)
    print(f"\nMatching {fname} ({norm_field}):")
    matched = False
    for line in lines:
        norm_line = normalize(line)
        if norm_field in norm_line or (len(norm_line) >= 4 and norm_line in norm_field):
            print(f"  [MATCH] '{line}' ({norm_line})")
            matched = True
            break
    if not matched:
        print("  [NO MATCH]")

