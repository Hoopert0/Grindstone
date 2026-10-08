"""A single-task Start goes to the training spot for the level, or for what was picked."""
from lumberjack.nav import training as T


def test_picked_fishing_method_goes_where_that_method_is():
    assert T.start_for("fishing", {"auto_fish": False, "fish_method": "net"}, 33)[0] == "★ Draynor fishing"
    assert T.start_for("fishing", {"auto_fish": False, "fish_method": "lure"}, 33)[0] == "★ Lumbridge river"
    assert T.start_for("fishing", {"auto_fish": False, "fish_method": "cage"}, 45)[0] == "★ Catherby fishing"


def test_by_level_picks_place_and_settings_and_the_next_tier():
    place, over, nxt = T.start_for("fishing", {"auto_fish": True, "fish_method": "net"}, 33)
    assert place == "★ Lumbridge river" and over["fish_method"] == "lure" and nxt == 40
    place, over, nxt = T.start_for("smithing", {}, 12)
    assert place == "★ Varrock anvil" and nxt is None


def test_picked_trees_ores_and_targets():
    assert T.start_for("woodcutting", {"auto_trees": False, "trees": ["willow"]}, 31)[0] == "★ Draynor willows"
    assert T.start_for("woodcutting", {"auto_trees": False, "trees": ["oak", "tree"]}, 40)[0] == "★ Lumbridge trees"
    assert T.start_for("mining", {"auto_ores": False, "ores": ["coal"]}, 35)[0] == "★ Barbarian mine"
    assert T.start_for("combat", {"targets": ["hill_giant"]}, 33)[0] == "★ Edgeville hill giants"
    assert T.start_for("thieving", {"thieve": ["guard"]}, 41)[0] == "★ Varrock guards"


def test_a_pick_not_on_the_route_or_no_route_starts_where_we_stand():
    assert T.start_for("mining", {"auto_ores": False, "ores": ["gold"]}, 40) == (None, {}, None)
    assert T.start_for("cooking", {}, 40) == (None, {}, None)
