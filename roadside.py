"""Roadside check: what NZ law says would happen if you were stopped driving at a given BAC.

A deterrent and an illustration of the law, not legal advice. Sources, read October 2026:
- NZTA, Alcohol and drug limits (limits, court penalties, interlock):
  https://nzta.govt.nz/driver-licences/driving-offences-and-penalties/alcohol-and-drug-limits
- NZTA, 28-day roadside licence suspension:
  https://nzta.govt.nz/driver-licences/driving-offences-and-penalties/driving-offences/28-day-roadside-suspension
- NZTA, Demerit points (50 points for the infringement offences):
  https://nzta.govt.nz/driver-licences/driving-offences-and-penalties/demerit-points
- Community Law Manual, Alcohol: the drink-driving offences ($200 infringement fee, typical fines):
  https://communitylaw.org.nz/community-law-manual/chapter-31-driving-and-traffic-law/drink-drug-driving/alcohol-the-drink-driving-offences/

NZ law pairs its breath and blood limits 5 to 1 (250 micrograms per litre of breath =
50 mg per 100 ml of blood, 400 = 80, 150 = 30, 800 = 160), so breath = blood x 5.
The app's BAC is a blood figure: 0.05% = 50 mg per 100 ml.
"""
from dataclasses import dataclass, field

ADULT_AGE = 20
ADULT_LIMIT_MG = 50
CRIMINAL_MG = 80
UNDER_20_INFRINGEMENT_MG = 30
ROADSIDE_SUSPENSION_MG = 130  # first offence; with a relevant conviction it's over 80
INTERLOCK_MG = 160
REPEAT = "A 3rd or later offence: up to 2 years' prison, up to $6,000 and more than a year off the road."


@dataclass
class Stop:
    level: str  # "clear", "infringement" or "court"
    headline: str
    details: list = field(default_factory=list)


def blood_mg(bac_percent):
    """BAC % (g per 100 ml) -> mg per 100 ml of blood."""
    return bac_percent * 1000


def breath_ug(bac_percent):
    """BAC % -> the equivalent micrograms per litre of breath, by NZ law's 5:1 pairing."""
    return blood_mg(bac_percent) * 5


def legal_limit(age=None):
    """The highest BAC % you can legally drive at: zero under 20, else the adult limit."""
    return 0.0 if age is not None and age < ADULT_AGE else ADULT_LIMIT_MG / 1000


def check(bac_percent, age=None):
    """What a first-time offender faces if stopped now. age None is treated as 20 or over."""
    blood, breath = blood_mg(bac_percent), breath_ug(bac_percent)
    young = age is not None and age < ADULT_AGE
    reading = f"About {breath:.0f} µg/L breath, {blood:.0f} mg/100 mL blood."

    if young and 0 < blood <= UNDER_20_INFRINGEMENT_MG:
        return Stop("infringement", "Infringement: $200 fine and 50 demerit points",
                    [reading, "Under 20 the limit is zero. No court and no criminal record, "
                              "a bit like a speeding ticket."])
    if young and UNDER_20_INFRINGEMENT_MG < blood <= CRIMINAL_MG:
        return Stop("court", "Court: up to 3 months' prison or a fine up to $2,250, "
                             "and at least 3 months off the road",
                    [reading, "Under 20 the limit is zero. You'd also get 50 demerit points "
                              "and a criminal record."])
    if blood <= 0 or (not young and blood <= ADULT_LIMIT_MG):
        under = "No alcohol: free to go." if blood <= 0 else "Under the adult limit: free to go."
        details = [reading] if blood > 0 else []
        if blood > 0:
            details.append("Under the limit isn't unimpaired: NZTA says even at the limit "
                           "you're twice as likely to crash.")
        if age is None and blood > 0:
            details.append("Assumes you're 20 or over; add your age to the profile. "
                           "Under 20 the limit is zero.")
        return Stop("clear", under, details)
    if blood <= CRIMINAL_MG:
        return Stop("infringement", "Infringement: $200 fine and 50 demerit points",
                    [reading, "No court and no criminal record, a bit like a speeding ticket."])

    details = [reading, f"A first offender's fine is usually about ${round(breath, -1):,.0f} "
                        "(the breath reading in dollars), plus court costs, and a criminal record."]
    if blood > ROADSIDE_SUSPENSION_MG:
        details.append("Licence suspended on the spot for 28 days.")
    if blood > INTERLOCK_MG:
        details.append("The court may order an alcohol interlock (with a 3-month "
                       "disqualification) instead.")
    details.append(REPEAT)
    return Stop("court", "Court: up to 3 months' prison or a fine up to $4,500, "
                         "and at least 6 months off the road", details)
