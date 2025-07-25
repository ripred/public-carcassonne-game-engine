from lib.interact.map import Map
from lib.interact.tile import Tile


def test_river_end_tile_fresh_instance_between_maps():
    m1 = Map()
    m1.start_river_phase()
    m1.place_river_end((5, 5), rotation=1)

    # Simulate starting a second map/game
    m2 = Map()
    m2.start_river_phase()

    tile = Tile.get_river_end_tile()
    assert tile.rotation == 0
    assert tile.placed_pos is None
