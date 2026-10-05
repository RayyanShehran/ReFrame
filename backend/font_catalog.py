"""Small, pinned redistributable font set; no system-font enumeration."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent / "fonts" / "candidates"
FACES = {
    "amiri-regular": (
        "Amiri-Regular.ttf",
        "Amiri",
        "Regular (400)",
        "ab391c4147d054c48976e98322ad0eefe1427aa0e0502a12a4c75d80a70cfcd7",
    ),
    "amiri-bold": (
        "Amiri-Bold.ttf",
        "Amiri",
        "Bold (700)",
        "cfccb794268e7d573d857e6d6a67f89cf8a053e8ffd85dfa0c8ec1bb36fc4827",
    ),
    "anton-regular": (
        "Anton-Regular.ttf",
        "Anton",
        "Regular (400)",
        "a4ba3a92350ebb031da0cb47630ac49eb265082ca1bc0450442f4a83ab947cab",
    ),
}


def asset(choice):
    return ROOT / FACES[choice][0]
