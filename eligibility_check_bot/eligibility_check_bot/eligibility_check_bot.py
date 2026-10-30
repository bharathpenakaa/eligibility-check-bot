"""
Eligibility Check Bot
=====================
Automates the X12 270 (eligibility inquiry) / 271 (eligibility response)
exchange used in healthcare claims.

Flow:  member details -> build 270 -> payer -> 271 -> parse -> readable result

The payer here is a MOCK so everything runs offline. To go live, replace
`MockPayer.submit()` with a call to your clearinghouse / payer API
(SFTP, SOAP, REST or CAQH CORE connectivity).

Usage:
    python eligibility_check_bot.py --demo
    python eligibility_check_bot.py --member-id M1001 --first Jane --last Doe \
        --dob 1985-04-12 --show-x12
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import datetime

# --------------------------------------------------------------------------
# X12 lookup tables (small subset, enough for a working bot)
# --------------------------------------------------------------------------
EB01_CODES = {
    "1": "Active Coverage",
    "2": "Active - Full Risk Capitation",
    "3": "Active - Services Capitated",
    "4": "Active - Services Capitated to PCP",
    "5": "Active - Pending Investigation",
    "6": "Inactive",
    "7": "Inactive - Pending Eligibility Update",
    "8": "Inactive - Pending Investigation",
    "A": "Co-Insurance",
    "B": "Co-Payment",
    "C": "Deductible",
    "G": "Out of Pocket (Stop Loss)",
    "I": "Non-Covered",
}
ACTIVE_CODES = {"1", "2", "3", "4", "5"}
INACTIVE_CODES = {"6", "7", "8"}

SERVICE_TYPES = {
    "30": "Health Benefit Plan Coverage",
    "98": "Professional (Physician) Visit - Office",
    "88": "Pharmacy",
    "AL": "Vision (Optometry)",
    "35": "Dental Care",
    "50": "Hospital - Inpatient",
}
TIME_PERIODS = {"23": "Calendar Year", "24": "Year to Date",
                "25": "Contract", "27": "Visit", "29": "Remaining"}
AAA_REASONS = {
    "71": "Patient birth date does not match payer records",
    "73": "Invalid/missing subscriber name",
    "75": "Subscriber/insured not found",
    "42": "Unable to respond at current time",
}


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------
@dataclass
class Member:
    member_id: str
    first_name: str
    last_name: str
    dob: str  # YYYY-MM-DD

    def validate(self) -> None:
        if not self.member_id.strip():
            raise ValueError("member_id is required")
        if not self.first_name.strip() or not self.last_name.strip():
            raise ValueError("first and last name are required")
        try:
            datetime.strptime(self.dob, "%Y-%m-%d")
        except ValueError:
            raise ValueError("dob must be in YYYY-MM-DD format") from None

    @property
    def dob_x12(self) -> str:
        return self.dob.replace("-", "")


@dataclass
class Benefit:
    category: str
    service: str = ""
    coverage_level: str = ""
    plan_name: str = ""
    period: str = ""
    amount: str = ""
    percent: str = ""


@dataclass
class EligibilityResult:
    member_id: str = ""
    member_name: str = ""
    payer_name: str = ""
    status: str = "UNKNOWN"          # ACTIVE | INACTIVE | REJECTED | UNKNOWN
    plan_begin: str = ""
    benefits: list[Benefit] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 56,
            f"Member   : {self.member_name} ({self.member_id})",
            f"Payer    : {self.payer_name or 'n/a'}",
            f"Status   : {self.status}",
        ]
        if self.plan_begin:
            lines.append(f"Plan from: {self.plan_begin}")
        for e in self.errors:
            lines.append(f"Error    : {e}")
        if self.benefits:
            lines.append("-" * 56)
            lines.append("Benefits:")
            for b in self.benefits:
                parts = [b.category]
                if b.service:
                    parts.append(f"[{b.service}]")
                if b.amount:
                    parts.append(f"${b.amount}")
                if b.percent:
                    parts.append(f"{b.percent}")
                if b.coverage_level:
                    parts.append(f"({b.coverage_level})")
                if b.period:
                    parts.append(f"- {b.period}")
                if b.plan_name:
                    parts.append(f"| {b.plan_name}")
                lines.append("  * " + " ".join(parts))
        lines.append("=" * 56)
        return "\n".join(lines)


# --------------------------------------------------------------------------
# 270 builder
# --------------------------------------------------------------------------
class X12Builder:
    ELEM, COMP, SEG = "*", ":", "~"

    def __init__(self, sender_id="CLINICBOT", receiver_id="MOCKPAYER",
                 provider_npi="1234567893", provider_name="DEMO CLINIC"):
        self.sender_id = sender_id
        self.receiver_id = receiver_id
        self.npi = provider_npi
        self.provider_name = provider_name

    def build_270(self, member: Member, service_type: str = "30",
                  control_number: int = 1) -> str:
        member.validate()
        now = datetime.now()
        d8, d6, t4 = now.strftime("%Y%m%d"), now.strftime("%y%m%d"), now.strftime("%H%M")
        ctrl9 = f"{control_number:09d}"
        ctrl4 = str(control_number)

        body = [
            f"ST*270*0001*005010X279A1",
            f"BHT*0022*13*{ctrl4}*{d8}*{t4}",
            "HL*1**20*1",
            f"NM1*PR*2*{self.receiver_id}*****PI*{self.receiver_id}",
            "HL*2*1*21*1",
            f"NM1*1P*2*{self.provider_name}*****XX*{self.npi}",
            "HL*3*2*22*0",
            "TRN*1*" + ctrl9 + "*9" + self.sender_id,
            f"NM1*IL*1*{member.last_name.upper()}*{member.first_name.upper()}"
            f"****MI*{member.member_id}",
            f"DMG*D8*{member.dob_x12}",
            f"DTP*291*D8*{d8}",
            f"EQ*{service_type}",
        ]
        se = f"SE*{len(body) + 1}*0001"
        segs = [
            "ISA*00*          *00*          "
            f"*ZZ*{self.sender_id:<15}*ZZ*{self.receiver_id:<15}"
            f"*{d6}*{t4}*^*00501*{ctrl9}*0*T*{self.COMP}",
            f"GS*HS*{self.sender_id}*{self.receiver_id}*{d8}*{t4}*{ctrl4}*X*005010X279A1",
            *body,
            se,
            f"GE*1*{ctrl4}",
            f"IEA*1*{ctrl9}",
        ]
        return self.SEG.join(segs) + self.SEG


# --------------------------------------------------------------------------
# 271 parser
# --------------------------------------------------------------------------
def _fmt_date(d8: str) -> str:
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}" if re.fullmatch(r"\d{8}", d8) else d8


def parse_271(raw: str) -> EligibilityResult:
    raw = raw.strip()
    if not raw.startswith("ISA") or len(raw) < 106:
        raise ValueError("Not a valid X12 interchange (missing/short ISA)")

    elem, seg_term = raw[3], raw[105]
    segments = [s.strip().split(elem) for s in raw.split(seg_term) if s.strip()]

    res = EligibilityResult()
    for seg in segments:
        tag = seg[0]
        get = lambda i: seg[i] if len(seg) > i else ""  # noqa: E731

        if tag == "NM1":
            if get(1) == "PR":
                res.payer_name = get(3)
            elif get(1) == "IL":
                res.member_name = f"{get(4).title()} {get(3).title()}".strip()
                if get(8) == "MI":
                    res.member_id = get(9)
        elif tag == "AAA":
            reason = AAA_REASONS.get(get(3), f"Payer reject code {get(3)}")
            res.errors.append(reason)
            res.status = "REJECTED"
        elif tag == "DTP" and get(1) in ("291", "346", "356"):
            res.plan_begin = _fmt_date(get(3))
        elif tag == "EB":
            code = get(1)
            if code in ACTIVE_CODES and res.status != "REJECTED":
                res.status = "ACTIVE"
            elif code in INACTIVE_CODES and res.status != "REJECTED":
                res.status = "INACTIVE"
            res.benefits.append(Benefit(
                category=EB01_CODES.get(code, f"Code {code}"),
                coverage_level={"IND": "Individual", "FAM": "Family"}.get(get(2), get(2)),
                service=SERVICE_TYPES.get(get(3), get(3)),
                plan_name=get(5),
                period=TIME_PERIODS.get(get(6), ""),
                amount=get(7),
                percent=f"{float(get(8)) * 100:.0f}%" if get(8) else "",
            ))
    return res


# --------------------------------------------------------------------------
# Mock payer (replace with a real clearinghouse call)
# --------------------------------------------------------------------------
MEMBER_DB = {
    "M1001": {"first": "JANE", "last": "DOE", "dob": "19850412", "active": True,
              "plan": "GOLD PPO", "start": "20240101"},
    "M2002": {"first": "JOHN", "last": "SMITH", "dob": "19791130", "active": False,
              "plan": "SILVER HMO", "start": "20220101"},
}


class MockPayer:
    name = "MOCKPAYER"

    def submit(self, x12_270: str) -> str:
        elem, seg_term = x12_270[3], x12_270[105]
        segs = [s.split(elem) for s in x12_270.split(seg_term) if s]
        il = next(s for s in segs if s[0] == "NM1" and s[1] == "IL")
        dmg = next(s for s in segs if s[0] == "DMG")
        last, first, mid = il[3], il[4], il[9]
        dob = dmg[2]

        rec = MEMBER_DB.get(mid)
        aaa = None
        if rec is None:
            aaa = "AAA*N**75*C"
        elif rec["dob"] != dob:
            aaa = "AAA*N**71*C"

        d8 = datetime.now().strftime("%Y%m%d")
        t4 = datetime.now().strftime("%H%M")
        body = [
            "ST*271*0001*005010X279A1",
            f"BHT*0022*11*1*{d8}*{t4}",
            "HL*1**20*1",
            f"NM1*PR*2*{self.name}*****PI*{self.name}",
            "HL*2*1*21*1",
            "NM1*1P*2*DEMO CLINIC*****XX*1234567893",
            "HL*3*2*22*0",
            f"NM1*IL*1*{last}*{first}****MI*{mid}",
        ]
        if aaa:
            body.append(aaa)
        else:
            body.append(f"DTP*291*D8*{rec['start']}")
            if rec["active"]:
                p = rec["plan"]
                body += [
                    f"EB*1*IND*30**{p}",
                    f"EB*B*IND*98**{p}*27*25",        # $25 copay per visit
                    f"EB*C*IND*30**{p}*23*1500",      # $1500 annual deductible
                    f"EB*A*IND*30**{p}*23**0.20",     # 20% coinsurance
                ]
            else:
                body.append(f"EB*6*IND*30**{rec['plan']}")
        body.append(f"SE*{len(body) + 1}*0001")

        isa = ("ISA*00*          *00*          "
               f"*ZZ*{self.name:<15}*ZZ*{'CLINICBOT':<15}"
               f"*{d8[2:]}*{t4}*^*00501*000000001*0*T*:")
        return "~".join([isa, "GS*HB*MOCKPAYER*CLINICBOT*" + d8 + "*" + t4 + "*1*X*005010X279A1",
                         *body, "GE*1*1", "IEA*1*000000001"]) + "~"


# --------------------------------------------------------------------------
# The bot
# --------------------------------------------------------------------------
class EligibilityCheckBot:
    def __init__(self, payer=None):
        self.payer = payer or MockPayer()
        self.builder = X12Builder(receiver_id=self.payer.name)
        self._counter = 0

    def check(self, member: Member, service_type: str = "30",
              show_x12: bool = False) -> EligibilityResult:
        self._counter += 1
        req = self.builder.build_270(member, service_type, self._counter)
        if show_x12:
            print("--- 270 REQUEST ---\n" + req.replace("~", "~\n"))
        resp = self.payer.submit(req)
        if show_x12:
            print("--- 271 RESPONSE ---\n" + resp.replace("~", "~\n"))
        return parse_271(resp)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Eligibility Check Bot (X12 270/271)")
    ap.add_argument("--demo", action="store_true", help="run sample checks")
    ap.add_argument("--member-id")
    ap.add_argument("--first")
    ap.add_argument("--last")
    ap.add_argument("--dob", help="YYYY-MM-DD")
    ap.add_argument("--service-type", default="30")
    ap.add_argument("--show-x12", action="store_true")
    args = ap.parse_args()

    bot = EligibilityCheckBot()

    if args.demo:
        samples = [
            Member("M1001", "Jane", "Doe", "1985-04-12"),    # active
            Member("M2002", "John", "Smith", "1979-11-30"),  # inactive
            Member("M9999", "Nobody", "Here", "1990-01-01"), # not found
            Member("M1001", "Jane", "Doe", "1985-04-13"),    # DOB mismatch
        ]
        for m in samples:
            print(bot.check(m, show_x12=args.show_x12).summary())
        return

    if not all([args.member_id, args.first, args.last, args.dob]):
        ap.error("provide --member-id, --first, --last and --dob (or use --demo)")
    member = Member(args.member_id, args.first, args.last, args.dob)
    print(bot.check(member, args.service_type, args.show_x12).summary())


if __name__ == "__main__":
    main()
