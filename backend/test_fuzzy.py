import re
from difflib import SequenceMatcher

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
    
    best_match = None
    best_score = 0
    best_line = None
    
    for line in lines:
        norm_line = normalize(line)
        
        # Exact substring match is best
        if norm_field in norm_line or (len(norm_line) >= 4 and norm_line in norm_field):
            score = 1.0
        else:
            # Fuzzy match
            score = SequenceMatcher(None, norm_field, norm_line).ratio()
            # Also check if it's highly similar to a substring of the line
            if len(norm_line) > len(norm_field):
                for i in range(len(norm_line) - len(norm_field) + 1):
                    sub_score = SequenceMatcher(None, norm_field, norm_line[i:i+len(norm_field)]).ratio()
                    score = max(score, sub_score)
        
        if score > best_score:
            best_score = score
            best_match = norm_line
            best_line = line
            
    print(f"  Best Match: '{best_line}' (score {best_score:.2f})")

