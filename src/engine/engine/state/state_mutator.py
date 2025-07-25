from typing import cast

from lib.interact.tile import NO_POINTS, TileModifier, StructureType
from engine.game.tile_subscriber import MonastaryNeighbourSubsciber
from engine.state.game_state import GameState

from lib.config.map_config import MONASTARY_IDENTIFIER
from lib.config.scoring import POINT_LIMIT
from lib.interface.events.event_player_bannned import EventPlayerBanned
from lib.interface.events.event_player_turn_started import EventPlayerTurnStarted
from lib.interface.events.event_player_won import EventPlayerWon
from lib.interface.events.event_river_phase_completed import EventRiverPhaseCompleted
from lib.interface.events.event_game_ended import (
    EventGameEndedCancelled,
    EventGameEndedPointLimitReached,
    EventGameEndedStaleMate,
)
from lib.interface.events.event_game_started import EventGameStarted
from lib.interface.events.event_player_drew_tiles import (
    EventPlayerDrewTiles,
    PublicEventPlayerDrewTiles,
)
from lib.interface.events.event_player_meeple_freed import EventPlayerMeepleFreed
from lib.interface.events.event_tile_placed import (
    EventStartingTilePlaced,
)
from lib.interface.events.moves.move_place_meeple import (
    MovePlaceMeeple,
    MovePlaceMeeplePass,
)
from lib.interface.events.moves.move_place_tile import MovePlaceTile
from lib.interface.events.typing import EventType


class StateMutator:
    def __init__(self, state: GameState) -> None:
        self.state = state

    def commit(self, event: EventType) -> None:
        self.state.event_history.append(event)

        match event:
            case EventGameStarted() as e:
                self._commit_event_game_started(e)

            case EventPlayerDrewTiles() as e:
                self._commit_player_drew_tiles(e)

            case EventPlayerMeepleFreed() as e:
                self._commit_event_player_meeple_freed(e)

            case EventStartingTilePlaced() as e:
                self._commit_event_starting_tile_placed(e)

            case MovePlaceTile() as e:
                self._commit_move_place_tile(e)

            case MovePlaceMeeple() as e:
                self._commit_move_place_meeple(e)

            case MovePlaceMeeplePass() as e:
                self._commit_move_place_meeple_pass(e)

            case PublicEventPlayerDrewTiles() as e:
                self._commit_public_player_drew_tiles(e)

            case EventGameEndedPointLimitReached() as e:
                self._commit_event_game_ended_point_limit(e)

            case EventGameEndedStaleMate() as e:
                self._commit_event_game_ended_stalemate(e)

            case EventGameEndedCancelled() as e:
                self._commit_event_game_ended_cancelled(e)

            case EventPlayerBanned() as e:
                self._commit_event_player_banned(e)

            case EventPlayerTurnStarted() as e:
                self._commit_event_player_turn_started(e)

            case EventPlayerWon() as e:
                self._commit_event_player_won(e)

            case EventRiverPhaseCompleted() as e:
                self._commit_event_river_phase_completed(e)

    def _commit_move_place_tile(self, move: MovePlaceTile) -> None:
        """
        Player Tile Placed Event
        """
        # Get tile from player hand
        tile = self.state.players[move.player_id].tiles.pop(move.player_tile_index)
        while tile.rotation != move.tile.rotation:
            tile.rotate_clockwise(1)

        self.state.map._grid[move.tile.pos[1]][move.tile.pos[0]] = tile
        self.state.map.placed_tiles.append(tile)
        if tile.straight_river():
            self.state.map.straight_rivers -= 1

        # Keep track of tile placed for meeple placement
        self.state.tile_placed = tile
        tile.placed_pos = move.tile.pos

        # Check for any complete connected componentes
        completed_components = self.state.get_completed_components(tile)

        player_point_limit = -1

        internal_edges_visited: set[str] = set()

        # Check for base/regular connected components
        for internal_edge, connected_component in completed_components.items():
            if internal_edge in internal_edges_visited:
                continue

            unique_connected_tiles = set([t for t, _ in connected_component])
            # Get number of unique tiles
            reward = StructureType.get_points(tile.internal_edges[internal_edge]) * len(
                unique_connected_tiles
            )

            structure_type = tile.internal_edges[internal_edge]

            for t in unique_connected_tiles:
                reward = TileModifier.apply_point_modifiers(t, structure_type, reward)

            players_rewarded: set[int] = set()

            for connected_tile, connected_edge in connected_component:
                connected_meeple = connected_tile.internal_claims[connected_edge]

                if connected_meeple is None:
                    continue

                assert connected_meeple.placed

                if connected_meeple.player_id in players_rewarded:
                    self.commit(
                        EventPlayerMeepleFreed(
                            player_id=connected_meeple.player_id,
                            reward=NO_POINTS,
                            tile=connected_meeple.placed._to_model(),
                            placed_on=connected_meeple.placed_edge,
                        )
                    )
                    connected_meeple._free_meeple()
                    continue

                self.state.players[connected_meeple.player_id].points += reward
                self.commit(
                    EventPlayerMeepleFreed(
                        player_id=connected_meeple.player_id,
                        reward=reward,
                        tile=connected_meeple.placed._to_model(),
                        placed_on=connected_meeple.placed_edge,
                    )
                )
                connected_meeple._free_meeple()

                if (
                    player_point_limit < 0
                    and self.state.players[connected_meeple.player_id].points
                    >= POINT_LIMIT
                ):
                    player_point_limit = connected_meeple.player_id

                players_rewarded.add(connected_meeple.player_id)

            # If meeples freed -> don't reclaim later
            if players_rewarded:
                self.state.tile_placed_claims.add(internal_edge)

            internal_edges_visited.add(internal_edge)

        # Check for monastary/special completed componentes
        for subscribed_complete in self.state.tile_publisher.check_notify(tile):
            for player_id, reward, t, reward_edge in subscribed_complete._reward():
                self.state.players[player_id].points += reward

                if (
                    self.state.players[player_id].points >= POINT_LIMIT
                    and player_point_limit < 0
                ):
                    player_point_limit = player_id

                meeple = t.internal_claims[reward_edge]
                assert meeple is not None

                self.commit(
                    EventPlayerMeepleFreed(
                        player_id=player_id,
                        reward=reward,
                        tile=t._to_model(),
                        placed_on=reward_edge,
                    )
                )
                meeple._free_meeple()

        if player_point_limit >= 0:
            self.commit(EventGameEndedPointLimitReached(player_id=player_point_limit))

    def _commit_move_place_meeple(self, move: MovePlaceMeeple) -> None:
        """
        Player Meeple Placed Event
        """
        player = self.state.players[move.player_id]
        assert self.state.tile_placed

        # self.state.tile_placed.internal_claims[move.placed_on] = move.player_id
        meeple = player._get_available_meeple()
        assert meeple is not None

        meeple._place_meeple(self.state.tile_placed, move.placed_on)

        completed_components = self.state.get_completed_components(
            self.state.tile_placed
        )

        # This segment checks if player placed a meeple on a completed tile
        if move.placed_on == MONASTARY_IDENTIFIER:
            tile_subsciber = MonastaryNeighbourSubsciber(
                move.tile.pos, player.id, self.state.tile_placed, move.placed_on
            )
            tile_subsciber.register_to(self.state.tile_publisher, self.state.map._grid)

            for subscribed_complete in self.state.tile_publisher.check_notify(
                self.state.tile_placed
            ):
                if not isinstance(subscribed_complete, MonastaryNeighbourSubsciber):
                    continue

                subscribed_complete = cast(
                    MonastaryNeighbourSubsciber, subscribed_complete
                )

                if move.tile.pos != subscribed_complete.center:
                    assert False
                    continue

                rewarded_set = subscribed_complete._reward()

                # In meeple placement phase only one should be rewarded
                assert len(rewarded_set) == 1
                player_id, reward, t, e = rewarded_set[0]
                self.state.players[player_id].points += reward

                meeple = t.internal_claims[e]
                assert meeple is not None
                assert player_id == move.player_id

                self.commit(
                    EventPlayerMeepleFreed(
                        player_id=player_id,
                        reward=reward,
                        tile=t._to_model(),
                        placed_on=e,
                    )
                )
                meeple._free_meeple()

        # Check the player completed a reguar component and claimed
        elif move.placed_on in completed_components:
            reward = StructureType.get_points(
                self.state.tile_placed.internal_edges[move.placed_on]
            ) * len(set([t for t, _ in completed_components[move.placed_on]]))

            player.points += reward
            self.commit(
                EventPlayerMeepleFreed(
                    player_id=player.id,
                    reward=reward,
                    tile=self.state.tile_placed._to_model(),
                    placed_on=move.placed_on,
                )
            )
            meeple._free_meeple()

        # Cleanup intermeidate state variables
        self.state.tile_placed = None
        self.state.tile_placed_claims = set()

        if self.state.players[move.player_id].points >= POINT_LIMIT:
            self.commit(EventGameEndedPointLimitReached(player_id=move.player_id))

    def _commit_move_place_meeple_pass(self, move: MovePlaceMeeplePass) -> None:
        # Cleanup intermeidate state variables
        self.state.tile_placed = None
        self.state.tile_placed_claims = set()

    def _commit_event_game_started(self, e: EventGameStarted) -> None:
        """
        Game Started Event
        Engine emits this event
        """
        pass

    def _commit_event_player_meeple_freed(self, e: EventPlayerMeepleFreed) -> None:
        """
        Player Meeple Freed Event
        Meeples are self update. No count tracked on engine
        """
        pass

    def _commit_event_starting_tile_placed(self, e: EventStartingTilePlaced) -> None:
        """
        Starting Tile Placed Event
        Engine emits this event
        """
        pass

    def _commit_event_game_ended_point_limit(
        self, e: EventGameEndedPointLimitReached
    ) -> None:
        self.state.game_over = True

    def _commit_player_drew_tiles(self, e: EventPlayerDrewTiles) -> None:
        """
        Player Drew Tiles Event
        Engine emits this event to a specific player
        """
        pass

    def _commit_public_player_drew_tiles(self, e: PublicEventPlayerDrewTiles) -> None:
        """
        Player Drew Tiles Event
        Engine censors this event to specific players
        """
        pass

    def _commit_event_game_ended_stalemate(self, e: EventGameEndedStaleMate) -> None:
        """
        Game Ended in Stalemate Event
        Engine emits this event to specific players
        """
        pass

    def _commit_event_game_ended_cancelled(self, e: EventGameEndedCancelled) -> None:
        """
        Game Ended in Cancellation Event
        Engine emits this event to specific players
        """
        pass

    def _commit_event_player_banned(self, e: EventPlayerBanned) -> None:
        """
        Game Ended in Player Ban Event
        Engine censors this event to specific player
        """
        pass

    def _commit_event_player_turn_started(self, e: EventPlayerTurnStarted) -> None:
        """
        Player Turn Started Event
        Engine emits this event
        """
        pass

    def _commit_event_player_won(self, e: EventPlayerWon) -> None:
        """
        Player Won Game Event
        Engine emits this event
        """
        pass

    def _commit_event_river_phase_completed(self, e: EventRiverPhaseCompleted) -> None:
        """
        River Phase Completed Event
        Engine emits this event
        """
        pass

    def _check_subscibers(self) -> None:
        pass
