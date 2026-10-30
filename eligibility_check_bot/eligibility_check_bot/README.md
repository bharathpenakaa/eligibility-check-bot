# Eligibility Check Bot

Automates the X12 270 (eligibility inquiry) / 271 (eligibility response) exchange.
Uses a mock payer, standard library only (Python 3.9+).

## Run
    python eligibility_check_bot.py --demo
    python eligibility_check_bot.py --member-id M1001 --first Jane --last Doe --dob 1985-04-12 --show-x12

## Go live
Replace `MockPayer.submit()` with a clearinghouse/payer API call and set the real
ISA/GS IDs and provider NPI in `X12Builder`.
