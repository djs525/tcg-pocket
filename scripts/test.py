import asyncio
from tcgdexsdk import TCGdex
import pprint

async def main():
    tcgdex = TCGdex("en")
    card_set = await tcgdex.set.get("A1")

    for resume in card_set.cards:
        card = await tcgdex.card.get(resume.id)
        pprint.pprint(vars(card))
        print("---")

asyncio.run(main())