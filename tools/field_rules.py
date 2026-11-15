"""How Danbooru tags are sorted into the node's fields.

Used only by ``build_db.py``.  The bulk of the work is done by the
Mexes/danbooru-tag-taxonomy slots; the rules here re-shape those slots into
the node's fields and fix the cases where a slot is too coarse.
"""

from __future__ import annotations

import re

# (id, label shown in the node, default number of tags for the dice button)
FIELDS = [
    ("character", "character", 1),
    ("subject", "subject", 2),
    ("hair", "hair", 3),
    ("eyes", "eyes", 1),
    ("body", "body", 2),
    ("outfit", "outfit", 2),
    ("top", "top", 3),
    ("bottom", "bottom", 2),
    ("legwear", "legs & feet", 2),
    ("accessories", "accessories", 3),
    ("expression", "expression", 2),
    ("pose", "pose", 2),
    ("view", "view", 1),
    ("background", "background", 2),
]

# Posts carrying these do not show a character's usual look, so they are left
# out of the "most common tags for this character" statistics.
ALT_LOOK_TAGS = frozenset("""
alternate_costume cosplay alternate_hairstyle alternate_hair_color alternate_hair_length
alternate_eye_color alternate_skin_color alternate_breast_size alternate_form adapted_costume
genderswap genderswap_(mtf) genderswap_(ftm) aged_down aged_up contemporary enmaided
personification animalization furrification dark_persona costume_switch hair_down
""".split())

# Posts carrying these are poorly suited as "a picture of one character":
# colours are not tagged, or the tags describe several panels.
POOL_EXCLUDE_TAGS = frozenset("""
monochrome greyscale comic 4koma 2koma 3koma multiple_views no_humans sketch lineart
spot_color partially_colored reference_sheet character_sheet chibi silent_comic
turnaround expressions multiple_4koma variations sepia text_focus
""".split())

SLOT_TO_FIELD = {
    "appearance:hair": "hair",
    "appearance:eyes": "eyes",
    "appearance:anatomy": "body",
    "appearance:demographics": "subject",
    "clothing:outfit": "outfit",
    "clothing:top": "top",
    "clothing:bottom": "bottom",
    "clothing:underwear": "outfit",
    "clothing:headwear": "accessories",
    "clothing:eyewear": "accessories",
    "clothing:legwear": "legwear",
    "clothing:footwear": "legwear",
    "clothing:accents": "accessories",
    "equipment:weapon": "accessories",
    "equipment:handheld": "accessories",
    "pose:camera": "view",
    "pose:posture": "pose",
    "pose:gesture": "pose",
    "pose:kinetic": "pose",
    "pose:combat": "pose",
    "pose:interaction": "pose",
    "pose:physiological": "pose",
    "pose:expression": "expression",
    "scenery:setting": "background",
    "scenery:props": "background",
    "scenery:atmosphere": "background",
    "creature:animal": "background",
}

# Danbooru wiki tag groups, used when the taxonomy has no slot for a tag.
GROUP_TO_FIELD = [
    ("hair_color", "hair"), ("hair_styles", "hair"), ("hair", "hair"),
    ("eyes_tags", "eyes"),
    ("skin_color", "body"), ("ears_tags", "body"), ("tail", "body"), ("wings", "body"),
    ("breasts_tags", "body"), ("body_parts", "body"), ("ass", "body"), ("shoulders", "body"),
    ("dress", "outfit"), ("sexual_attire", "outfit"), ("nudity", "outfit"),
    ("sleeves", "top"),
    ("legwear", "legwear"),
    ("headwear", "accessories"), ("handwear", "accessories"), ("eyewear", "accessories"),
    ("piercings", "accessories"), ("embellishment", "accessories"), ("mask", "accessories"),
    ("posture", "pose"), ("gestures", "pose"), ("hands", "pose"),
    ("backgrounds", "background"), ("locations", "background"), ("lighting", "background"),
    ("flowers", "background"), ("water", "background"), ("fire", "background"),
    ("focus_tags", "view"),
]

# Tags that describe the picture rather than the character or the scene.
DROP = re.compile(r"""^(
    .*_\(cosplay\) | .*_\(meme\) | .*censor.* | .*watermark.* | .*_username | .*_logo |
    .*_text | .*speech_bubble.* | spoken_.* | .*_commentary | alternate_.* | official_alternate_.* |
    .*_connection | .*_namesake | virtual_youtuber | cosplay | adapted_costume | costume_switch |
    .*_focus | dated | signature | .*_name | cover.* | .*koma | .*_parody | parody | meme |
    multiple_(girls|boys|others|views|persona|4koma|expressions) | \d\+?(girls|boys|others) | 6\+.* | everyone | .*_request | twitter_.* |
    sound_effects | emphasis_lines | motion_lines | speed_lines | .*_bubble | border | .*_border |
    letterboxed | pillarboxed | .*_inset | inset | zoom_layer | .*_layer | projected_inset |
    no_humans | unworn_.* | .*_and_(sister|brother|daughter|son|wife|husband|niece|nephew)s? | age_difference |
    interracial | clothed_.*_nude_.* | mixed-sex_bathing | people | too_many | .*_theme | enmaided |
    hetero | yuri | yaoi | loli | shota | lolicon | shotacon | .*_challenge | bkub_.*
)$""", re.X)

COLORS = ("aqua black blonde blue brown dark_blue dark_green dark_red green grey light_blue light_brown "
          "light_green light_purple orange pink platinum_blonde purple red silver white yellow lavender "
          "tan beige").split()

_C = "(?:" + "|".join(COLORS) + r"|two-tone|multicolored|striped|plaid|checkered|polka_dot|print|frilled|" \
     r"torn|open|leather|denim|fur-trimmed|lace-trimmed|ribbon-trimmed|see-through|sleeveless|" \
     r"short|long|pleated|high-waist|striped|floral_print|animal_print|camouflage|gold|rainbow)"

# (pattern, field) - first match wins.  Applied before the taxonomy slot.
PATTERNS = [(re.compile(p), f) for p, f in [
    # --- who
    (r"^(1girl|1boy|1other|solo)$", "subject"),
    # --- gaze belongs with the pose, not the camera
    (r"^(looking_.*|eye_contact|facing_.*|sideways_glance|staring|.*_looking_.*)$", "pose"),
    # --- framing
    (r"^(upper_body|full_body|lower_body|cowboy_shot|portrait|close-up|wide_shot|very_wide_shot|"
     r"head_only|cropped_(?:torso|legs|shoulders|head|arms?)|.*_out_of_frame|from_.*|dutch_angle|pov|straight-on|profile|"
     r"depth_of_field|blurry_.*|blurry|bokeh|fisheye|foreshortening|perspective|.*_shot|"
     r"back_view|rear_view|three_quarter_view|vanishing_point)$", "view"),
    # --- hair accessories are accessories, the rest of *hair* is hair
    (r"^(hair_ornament|hair_ribbon|hair_bow|hair_flower|hairclip|hairband|hairpin|hair_bobbles|"
     r"hair_tie|hair_scrunchie|scrunchie|hair_bell|hair_tubes|hair_beads|hair_stick|hair_rings?|"
     r"hair_bands?|.*_hairband|.*_hair_ornament|.*_hairclip|hair_ears|hair_horns|.*_scrunchie|"
     r"hair_feathers|hair_tie_in_mouth|.*_hair_ribbon|.*_hair_bow|.*_hair_flower|.*_hair_tie|"
     r"hair_censor|hair_dryer|hairbrush|hair_pulled_back_tie|hair_cubes|hair_pods|hair_weapon)$",
     "accessories"),
    (r"^(hair_(?:pull|grab|flip|tucking|twirling|brushing|dressing|cutting|slicked_back_gesture)|"
     r"playing_with_.*hair|adjusting_hair|tying_hair|holding_.*hair|hand_in_.*hair|.*_hair_pull|"
     r"tucking_hair|bunching_hair|brushing_hair|washing_hair|drying_hair|smelling_hair|"
     r"cutting_hair|braiding_hair|biting_hair|hair_in_.*mouth|grabbing_another's_hair|cum_on_hair|"
     r"holding_another's_hair|hand_in_another's_hair|hand_on_own_hair)$", "pose"),
    (r"^(pubic_hair|.*_pubic_hair|armpit_hair|chest_hair|leg_hair|arm_hair|body_hair|facial_hair|"
     r"hairy.*|.*_facial_hair|beard|mustache|stubble|goatee|sideburns|long_sideburns)$", "body"),
    (r".*_hair$|^hair_.*|.*_bangs$|^bangs.*|.*twintails$|.*ponytail$|.*_braids?$|^braid.*|"
     r".*_bun$|.*_buns$|^ahoge$|.*_ahoge$|^sidelocks$|.*_sidelocks$|.*_drills?$|^antenna_hair$|"
     r"^forehead$|^bob_cut$|^hime_cut$|^pixie_cut$|^bowl_cut$|^undercut$|^mohawk$|^afro$|"
     r"^dreadlocks$|^cornrows$|^ringlets$|^bald.*|^widow's_peak$|^.*_hairstyle$|^updo$|^topknot$|"
     r"^cowlick$|^flipped_hair$|^.*_hair_.*$", "hair"),
    # --- eyes
    (r"^(eyeshadow|eyeliner|mascara|.*_eyeshadow|.*_eyeliner|makeup|lipstick|(?!parted).*_lips|"
     r".*_lipstick|lipgloss)$", "accessories"),
    (r"^(closed_eyes|one_eye_closed|half-closed_eyes?|wide-eyed|rolling_eyes|crossed_eyes|"
     r"squinting|narrowed_eyes|eyes_closed|.*_one_eye_closed|wince|winking.*|@_@|>_<|\^_\^|"
     r"=_=|\._\.|o_o|0_0|x_x|\+_\+|;\)|.*_stare|glaring|crying.*|tears|teardrop|tearing_up|"
     r"streaming_tears|happy_tears|.*_eyes_closed|eyes_visible_through_hair)$", "expression"),
    (r".*_eyes$|.*_pupils$|.*_sclera$|.*_irises?$|^heterochromia$|^tsurime$|^tareme$|^jitome$|"
     r"^sanpaku$|^eyelashes$|.*_eyelashes$|^eyebrows$|.*_eyebrows$|^slit_pupils$|^pupils$|"
     r"^.*-shaped_pupils$|^no_pupils$|^dilated_pupils$|^constricted_pupils$|^ringed_eyes$|"
     r"^aegyo_sal$|^bags_under_eyes$|^eye_trail$|^third_eye$|^extra_eyes$|^one-eyed$|^cyclops$|"
     r"^mole_under_eye$|^.*_under_eyes?$", "eyes"),
    # --- things worn on the neck count as part of the top
    (r"^(?:" + _C + r"_)?(necktie|bowtie|neckerchief|ascot|scarf|sailor_collar|neck_ribbon|"
     r"neck_ruff|bolo_tie|cravat|jabot|bow_tie|shawl|capelet|cape|cloak|poncho)$", "top"),
    # --- underwear: bras with the top, panties with the bottom
    (r"^(?:.*_)?(bra|sports_bra|bikini_top|bikini_top_only|sarashi|pasties|no_bra)$", "top"),
    (r"^(?:.*_)?(panties|thong|g-string|fundoshi|briefs|boxers|bikini_bottom|bikini_bottom_only)$|"
     r"^(no_panties|panty_pull|panties_aside|pantyshot|side-tie_panties|panties_around_.*)$", "bottom"),
    (r"^(garter_belt|garter_straps|zettai_ryouiki|thigh_belt|thigh_strap)$", "legwear"),
    # --- misc strong rules
    (r"^.*_halo$", "body"),
    (r"^multiple_(tails|wings|horns|moles|scars|piercings|earrings|rings|belts|braids)$", "body"),
    (r"^(?!1girl|1boy|magical_girl|.*_and_.*).*_(girl|boy)$|^(oni|angel|mermaid|merfolk|tomboy|"
     r"mecha_musume|ninja|knight|idol|nekomata|jiangshi|draph|miqo'te|kemonomimi_mode)$", "subject"),
    (r"^(bandaid_on_.*|bandaged_.*|bandage_on_.*|bandage_over_.*|.*_bandages?)$", "accessories"),
    (r"^(covering_.*|covered_(eyes|mouth|face))$", "pose"),
    (r"^covered_(navel|nipples|collarbone|abs|clitoris|penis|anus|pussy|erect_nipples)$", "body"),
    (r"^(?!hair_).*_flower$|^.*_rose$|^.*_petals$|^.*_leaves$|^.*_tree$", "background"),
    (r"^.*_print$|^(polka_dot|camouflage|leather|latex|plaid|checkered|argyle|pinstripe_pattern)$", "outfit"),
    (r"^(light_blush|anger_vein|parted_lips|blush_stickers|torogao|.*_blush)$", "expression"),
    (r"^(carrying|smoking|drinking|eating|singing|bathing|floating|mouth_hold|sheet_grab|"
     r"invisible_chair|indian_style|lifting_own_clothes|clothing_aside|dress_lift|adjusting_.*|"
     r"holding_own_.*|holding_unworn_.*|breast_press|presenting.*|.*_grab|.*_lift|.*_pull|"
     r"grabbing_.*|tachi-e)$", "pose"),
    (r"^(black_fur|.*_fur|beard_stubble|navel_hair)$", "body"),
]]

# Clothing name patterns, used only when the taxonomy has no slot for a tag.
FALLBACK_PATTERNS = [(re.compile(p), f) for p, f in [
    (r"^(?:.*_)?(shirt|blouse|jacket|coat|sweater|hoodie|cardigan|vest|blazer|t-shirt|tank_top|"
     r"camisole|crop_top|tube_top|halterneck|bandeau|bustier|corset|waistcoat|parka|raincoat|"
     r"trench_coat|labcoat|haori|sleeves|sleeve|turtleneck|tunic|jersey|polo_shirt|breastplate|"
     r"pauldrons?|shoulder_armor|bra|sarashi|chest_sarashi|pasties|shrug_\(clothing\)|shrug|"
     r"happi|tabard|suit_jacket|track_jacket|letterman_jacket|bomber_jacket|hooded_.*|"
     r"sailor_shirt|serafuku_top|collared_.*|neckline|lapels|epaulettes|aiguillette)$", "top"),
    (r"^(bare_shoulders|off_shoulder|off-shoulder_.*|sleeveless|strapless|bare_arms|"
     r"shoulder_cutout|cleavage_cutout|underboob_cutout|clothing_cutout|side_cutout|"
     r"back_cutout|backless_.*|plunging_neckline|center_opening|open_shirt|open_jacket|"
     r"open_coat|open_cardigan|open_vest|open_collar|unbuttoned.*|partially_unbuttoned|"
     r"jacket_on_shoulders|.*_on_shoulders|coat_on_shoulders|sleeves_.*|sleeve_.*|"
     r"buttons|double-breasted|breast_pocket|zipper.*|drawstring|hood|hood_up|hood_down|"
     r"shirt_tucked_in|tied_shirt|midriff_peek|taut_shirt|taut_clothes|impossible_shirt|"
     r"impossible_clothes|shirt_lift|.*_removed|single_bare_shoulder|armpit_cutout|"
     r"detached_collar|wing_collar|popped_collar|high_collar|v-neck|.*_neckwear)$", "top"),
    (r"^(?:.*_)?(skirt|pants|shorts|jeans|trousers|bloomers|buruma|hakama|hakama_skirt|"
     r"hakama_pants|panties|thong|g-string|fundoshi|loincloth|briefs|boxers|boxer_briefs|"
     r"leggings|capri_pants|sweatpants|overskirt|pelvic_curtain|miniskirt|microskirt|"
     r"bike_shorts|short_shorts|hot_pants|dolphin_shorts|gym_shorts|denim_shorts|"
     r"pencil_skirt|pleated_skirt|side_slit|waist_apron|sarong|showgirl_skirt|chaps|"
     r"hip_vent|bikini_bottom|.*_bikini_bottom|lowleg_.*|belt)$", "bottom"),
    (r"^(bottomless|no_pants|no_panties|pantyshot|upskirt|skirt_lift|skirt_hold|pants_pull|"
     r"panty_pull|skirt_pull|shorts_pull|panties_aside|skirt_set|high-waist_.*|lowleg|"
     r"side-tie_.*|string_panties|cameltoe|whale_tail_\(clothing\)|panty_peek)$",
     "bottom"),
    (r"^(?:.*_)?(thighhighs|pantyhose|socks|kneehighs|stockings|legwear|leg_warmers|"
     r"footwear|boots|shoes|sandals|sneakers|loafers|slippers|high_heels|heels|pumps|geta|"
     r"zouri|tabi|mary_janes|flip-flops|greaves|garter_straps|garter_belt|thigh_strap|"
     r"anklet|ankle_.*|toeless_.*|stirrup_.*|uwabaki|okobo|waraji|thigh_boots|knee_boots|"
     r"fishnets|leg_garter|thighlet|leg_ribbon|bobby_socks|loose_socks|tights)$", "legwear"),
    (r"^(barefoot|no_shoes|no_socks|bare_legs|shoes_removed|.*_soles|shoe_soles|"
     r"single_thighhigh|single_sock|single_shoe|single_kneehigh|skindentation|"
     r"asymmetrical_legwear|mismatched_legwear|.*_over_.*hose|torn_legwear)$", "legwear"),
    (r"^(?:.*_)?(dress|kimono|yukata|uniform|serafuku|swimsuit|bikini|leotard|bodysuit|"
     r"armor|robe|pajamas|nightgown|negligee|babydoll|lingerie|costume|suit|tuxedo|overalls|"
     r"jumpsuit|unitard|romper|apron|gown|sundress|cheongsam|china_dress|hanfu|hanbok|"
     r"dirndl|furisode|sweater_dress|pinafore_dress|tracksuit|wetsuit|plugsuit|maid|"
     r"playboy_bunny|bunnysuit|gakuran|miko|nontraditional_miko|habit|nun|teddy_\(clothing\)|"
     r"chemise|one-piece|monokini|slingshot_swimsuit|sportswear|gym_uniform|track_suit|"
     r"naked_.*|nude|completely_nude|topless.*|underwear_only|underwear|clothes|.*_clothes|"
     r"japanese_clothes|chinese_clothes|bridal_.*|wedding_dress|.*_outfit|.*_attire|ao_dai|"
     r"vestments|straitjacket|harness|bondage_outfit|bodystocking|bodypaint|fur_trim|frills|"
     r"lace_trim|lace|ribbon_trim|gold_trim|see-through|wet_clothes|torn_clothes|"
     r"skin_tight|revealing_clothes|highleg.*|leotard_under_clothes|.*_under_clothes)$", "outfit"),
]]

# Hand-checked fixes for popular tags.  ``None`` drops the tag.
OVERRIDES = {
    # body: traits the taxonomy filed elsewhere
    "halo": "body", "mechanical_halo": "body", "nail_polish": "accessories",
    "heart": "background", "star_(symbol)": "background", "sparkle": "background",
    "mole": "body", "mole_under_mouth": "body", "scar": "body", "tattoo": "body",
    "freckles": "body", "abs": "body", "muscular": "body", "muscular_female": "body",
    "muscular_male": "body", "dark-skinned_female": "body", "dark-skinned_male": "body",
    "fang": "body", "fangs": "body", "skin_fang": "body", "teeth": "expression",
    "upper_teeth_only": "expression", "tongue": "expression", "tongue_out": "expression",
    "sharp_teeth": "body", "pointy_ears": "body", "thick_eyebrows": "eyes",
    "short_eyebrows": "eyes", "forehead_mark": "body", "facial_mark": "body",
    "whisker_markings": "body", "horse_girl": "subject", "maid": "outfit",
    # clothing
    "gloves": "accessories", "bell": "accessories", "neck_bell": "accessories",
    "choker": "accessories", "collar": "accessories", "necklace": "accessories",
    "piercing": "accessories", "bandages": "accessories", "bandaid": "accessories",
    "school_uniform": "outfit", "serafuku": "outfit", "open_clothes": "outfit",
    "clothes_lift": "pose", "clothes_pull": "pose", "undressing": "pose",
    "bare_shoulders": "top", "collarbone": "body", "midriff": "body", "navel": "body",
    "cleavage": "body", "floral_print": "outfit", "wide_sleeves": "top",
    "shirt": "top", "dress": "outfit", "skirt": "bottom", "frills": "outfit",
    "hat": "accessories", "bow": "accessories", "ribbon": "accessories",
    "jewelry": "accessories", "earrings": "accessories", "wings": "body", "tail": "body",
    "apron": "outfit", "armor": "outfit", "kimono": "outfit", "wet": "body",
    "sweat": "body", "blood": "body", "weapon": "accessories",
    # pose / expression
    "holding": "pose", "blush": "expression", "smile": "expression",
    "sweatdrop": "expression", "on_bed": "pose", "on_back": "pose", "on_floor": "pose",
    "on_ground": "pose", "on_chair": "pose", "on_couch": "pose", "head_rest": "pose",
    "hands_up": "pose", "trembling": "pose", "breath": "expression",
    # scene
    "rose": "background", "white_flower": "background", "flower": "background",
    "petals": "background", "shadow": "background",
    # not useful as prompt building blocks
    "cross": "accessories", "staff": "accessories", "headset": "accessories",
    "stuffed_toy": "accessories", "stuffed_animal": "accessories", "doll": "accessories",
    "jingle_bell": "accessories", "ear_covers": "accessories", "holster": "accessories",
    "gohei": "accessories", "facepaint": "accessories", "sheath": "accessories",
    "arm_belt": "accessories", "one_eye_covered": "hair", "musical_note": "background",
    "magical_girl": "outfit", "casual": "outfit", "police": "outfit", "pocket": "outfit",
    "clothes_writing": "outfit", "feathers": "accessories", "reflection": "background",
    "partially_submerged": "background", "christmas": "background", "halloween": "background",
    "new_year": "background", "valentine": "background", "crystal": "background",
    "sand": "background", "branch": "background", "air_bubble": "background",
    "blanket": "background", "brick_wall": "background", "bamboo": "background",
    "vines": "background", "tile_wall": "background", "tiles": "background",
    "condom": None, "used_condom": None, "holding_condom": None,
    "solo_focus": None, "male_focus": None, "aged_down": None, "aged_up": None,
    "genderswap": None, "genderswap_(mtf)": None, "genderswap_(ftm)": None,
    "contemporary": None, "siblings": None, "sisters": None, "brothers": None,
    "twins": None, "couple": None, "character_name": None, "copyright_name": None,
    "oral": None, "dated": None,
}

# In a character preset, only one tag of each group is kept (the most common
# one) unless one of the ``multi`` tags is also typical for the character.
EXCLUSIVE_GROUPS = {
    "hair_color": dict(
        tags=("black_hair blonde_hair brown_hair blue_hair pink_hair white_hair grey_hair "
              "purple_hair red_hair green_hair orange_hair aqua_hair light_brown_hair "
              "light_blue_hair dark_blue_hair light_purple_hair platinum_blonde_hair "
              "dark_green_hair light_green_hair dark_red_hair").split(),
        multi="multicolored_hair two-tone_hair streaked_hair gradient_hair colored_inner_hair "
              "split-color_hair colored_tips".split()),
    "hair_length": dict(
        tags="very_short_hair short_hair medium_hair long_hair very_long_hair absurdly_long_hair bald".split(),
        multi=()),
    "eye_color": dict(
        tags=("blue_eyes red_eyes purple_eyes green_eyes brown_eyes yellow_eyes pink_eyes "
              "black_eyes grey_eyes orange_eyes aqua_eyes white_eyes").split(),
        multi="heterochromia multicolored_eyes two-tone_eyes gradient_eyes".split()),
    "breast_size": dict(
        tags="flat_chest small_breasts medium_breasts large_breasts huge_breasts gigantic_breasts".split(),
        multi=()),
    "subject": dict(tags="1girl 1boy 1other".split(), multi=()),
    "framing": dict(tags="portrait upper_body cowboy_shot full_body lower_body close-up".split(), multi=()),
    "background_color": dict(
        tags=("white_background grey_background blue_background pink_background black_background "
              "yellow_background red_background green_background purple_background "
              "orange_background brown_background aqua_background").split(),
        multi="two-tone_background gradient_background multicolored_background".split()),
    "posture": dict(tags="standing sitting lying kneeling squatting all_fours".split(), multi=()),
}

# Tags that say what is visible rather than what the character is like.  They
# stay available for autocomplete and random picks but are never part of a
# character preset.
NOT_IDENTITY = frozenset("""
breasts navel cleavage collarbone thighs midriff armpits ass stomach legs feet toes
sideboob underboob thigh_gap ribs back bare_back shoulder_blades nipples pussy penis
areolae covered_nipples covered_navel groin navel_peek toenails fingernails lips nose
teeth tongue sweat wet blood ass_visible_through_thighs median_furrow knees kneepits
soles hands arms neck nape feet_out_of_frame cameltoe pubic_hair testicles anus
hip_bones dimples_of_venus linea_alba tan tanlines one-piece_tan bikini_tan
bare_shoulders bare_arms bare_legs barefoot nude completely_nude
""".split())


# Jio7/danbooru-tags-classified categories, the last resort.
JIO7_TO_FIELD = {"attire": "accessories", "feature": "body", "action": "pose",
                 "setting": "background", "expression": "expression", "object": "accessories"}

# Never part of a character preset: too generic to say anything on their own.
NOT_IDENTITY = NOT_IDENTITY | frozenset("holding weapon food animal zettai_ryouiki skindentation".split())


def classify_tag(name, slot, wiki_groups, jio7=None):
    """Return the field id for a general tag, or ``None`` to leave it out."""
    if name in OVERRIDES:
        return OVERRIDES[name]
    if DROP.match(name):
        return None
    for pattern, field in PATTERNS:
        if pattern.match(name):
            return field
    field = SLOT_TO_FIELD.get(slot)
    if field:
        return field
    if slot and slot.startswith("other:") and slot != "other:unclassified":
        return None
    for pattern, field in FALLBACK_PATTERNS:
        if pattern.match(name):
            return field
    for group, field in GROUP_TO_FIELD:
        if group in wiki_groups:
            return field
    if name.startswith("holding_"):
        return "pose"
    return JIO7_TO_FIELD.get(jio7)
