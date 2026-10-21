"""Rules retained from fk8663212/bingobingo, commit e86191a (see docs/BINGO.md)."""
import secrets

LINES = ([list(range(row * 5, row * 5 + 5)) for row in range(5)]
         + [[column + row * 5 for row in range(5)] for column in range(5)]
         + [[0, 6, 12, 18, 24], [4, 8, 12, 16, 20]])


def make_card():
    values = secrets.SystemRandom().sample(range(1, 50), 24)
    values.insert(12, 0)
    return values


def completed_lines(card, drawn):
    return [line for line in LINES if all(card[index] == 0 or card[index] in drawn for index in line)]
