"""Small, pinned redistributable font set; no system-font enumeration."""

from pathlib import Path
from typing import Literal

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
    "barlowcondensed-regular": (
        "BarlowCondensed-Regular.ttf",
        "Barlow Condensed",
        "Regular (400)",
        "583cec5da3b84bc4dc7c9c72e2a565c94d34e431518b19d7e250b7830ad5f996",
    ),
    "barlowcondensed-bold": (
        "BarlowCondensed-Bold.ttf",
        "Barlow Condensed",
        "Bold (700)",
        "e476562ec9c1e16cf16475895b511f08c804f438cc9a9f80a44ea50a0eeb5b65",
    ),
    "barlowcondensed-italic": (
        "BarlowCondensed-Italic.ttf",
        "Barlow Condensed",
        "Italic (400)",
        "063615da0a64f318397de2afac330dfbfbbf4cdb562a9987cb72bfdcbd37ba7c",
    ),
    "poppins-regular": (
        "Poppins-Regular.ttf",
        "Poppins",
        "Regular (400)",
        "7e65201e9b79159e2300267cc885e16c8dcef2424cdfa09a29bfb0980a94a7ba",
    ),
    "poppins-bold": (
        "Poppins-Bold.ttf",
        "Poppins",
        "Bold (700)",
        "983676516167748b74de6f4771fb384c664fd913acb8b471122ecacf5da5ea6c",
    ),
    "poppins-italic": (
        "Poppins-Italic.ttf",
        "Poppins",
        "Italic (400)",
        "4fa76ae75b40f926420514044722cb97f32186cafd3b38263cc34dad7174d46d",
    ),
    "varelaround-regular": (
        "VarelaRound-Regular.ttf",
        "Varela Round",
        "Regular (400)",
        "e1e47eb66dbc2ddc106661338e712d9176c9e83c669a82fde155324823d03aa2",
    ),
    "spacemono-regular": (
        "SpaceMono-Regular.ttf",
        "Space Mono",
        "Regular (400)",
        "95837e182baeeada83368f7748db28357f0a1b75c6b84ff7065b5edf933c8e18",
    ),
    "spacemono-bold": (
        "SpaceMono-Bold.ttf",
        "Space Mono",
        "Bold (700)",
        "405e73d41afb7e5906efce206a326af5c956f38e255f35421c260e861e599c59",
    ),
    "spacemono-italic": (
        "SpaceMono-Italic.ttf",
        "Space Mono",
        "Italic (400)",
        "eca264c8690a91fe837d19a6badf2d88961610c4c8aafcb196526192a9f258c5",
    ),
    "crimsontext-regular": (
        "CrimsonText-Regular.ttf",
        "Crimson Text",
        "Regular (400)",
        "48e6c5d5ad1d01599d374ecb817e15890d1feb3b8a3a88e527d44c90389e1f06",
    ),
    "crimsontext-bold": (
        "CrimsonText-Bold.ttf",
        "Crimson Text",
        "Bold (700)",
        "a3a0765fc5e8d0b49b540a23aefe0184887dd79f06a0bdf4db7035cea6befa93",
    ),
    "crimsontext-italic": (
        "CrimsonText-Italic.ttf",
        "Crimson Text",
        "Italic (400)",
        "4ed1699ac7c64e8b3d33f6bb8323c3d7206b0d7bacb9ee2d65c697e6014d29de",
    ),
    "patrickhand-regular": (
        "PatrickHand-Regular.ttf",
        "Patrick Hand",
        "Regular (400)",
        "0f173b3e6cb6d1af25babf7f0057c5ac4ee11f9992b0469bb817e967ef4ad0fc",
    ),
}


def asset(choice):
    return ROOT / FACES[choice][0]


Choice = Literal[
    "patrickhand-regular",
    "amiri-regular",
    "amiri-bold",
    "anton-regular",
    "barlowcondensed-regular",
    "barlowcondensed-bold",
    "barlowcondensed-italic",
    "poppins-regular",
    "poppins-bold",
    "poppins-italic",
    "varelaround-regular",
    "spacemono-regular",
    "spacemono-bold",
    "spacemono-italic",
    "crimsontext-regular",
    "crimsontext-bold",
    "crimsontext-italic",
]
