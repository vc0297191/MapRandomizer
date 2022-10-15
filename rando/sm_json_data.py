import abc
from typing import Set, Dict, List
import json
import logging
import pathlib
from dataclasses import dataclass

@dataclass
class DifficultyConfig:
    tech: Set[str]  # Set of names of enabled tech (https://github.com/miketrethewey/sm-json-data/blob/master/tech.json)
    shine_charge_tiles: int  # Minimum number of tiles required to shinespark

@dataclass
class GameState:
    difficulty: DifficultyConfig
    items: Set[str]   # Set of collected items
    flags: Set[str]   # Set of activated flags
    # max_missiles: int
    # max_super_missiles: int
    # max_power_bombs: int
    # current_missiles: int
    # current_super_missiles: int
    # current_power_bombs: int
    node_index: int  # Current node (representing room and location within room)


class Condition:
    @abc.abstractmethod
    def is_accessible(self, state: GameState) -> bool:
        raise NotImplemented

# def get_plm_type_item_index(plm_type):
#     assert 0xEED7 <= plm_type <= 0xEFCF
#     assert plm_type % 4 == 3
#     i = ((plm_type - 0xEED7) // 4) % 21
#     return i

class ConstantCondition(Condition):
    def __init__(self, cond: bool):
        self.cond = cond

    def is_accessible(self, state: GameState) -> bool:
        return self.cond

    def __repr__(self):
        return str(self.cond)


class TechCondition(Condition):
    def __init__(self, tech: str):
        self.tech = tech

    def is_accessible(self, state: GameState) -> bool:
        return self.tech in state.difficulty.tech

    def __repr__(self):
        return "Tech(" + self.tech + ")"


class ShineChargeCondition(Condition):
    def __init__(self, tiles: int):
        self.tiles = tiles

    def is_accessible(self, state: GameState) -> bool:
        return "SpeedBooster" in state.items and self.tiles >= state.difficulty.shine_charge_tiles

class ItemCondition(Condition):
    def __init__(self, item: str):
        self.item = item

    def is_accessible(self, state: GameState) -> bool:
        return self.item in state.items

    def __repr__(self):
        return "Item(" + self.item + ")"


class FlagCondition(Condition):
    def __init__(self, flag: str):
        self.flag = flag

    def is_accessible(self, state: GameState) -> bool:
        return self.flag in state.flags


class AndCondition(Condition):
    def __init__(self, conditions):
        self.conditions = conditions

    def is_accessible(self, state: GameState) -> bool:
        return all(cond.is_accessible(state) for cond in self.conditions)

    def __repr__(self):
        return "And(" + ','.join(str(c) for c in self.conditions) + ")"

class OrCondition(Condition):
    def __init__(self, conditions):
        self.conditions = conditions

    def is_accessible(self, state: GameState) -> bool:
        return any(cond.is_accessible(state) for cond in self.conditions)

    def __repr__(self):
        return "Or(" + ','.join(str(c) for c in self.conditions) + ")"


# Helper function to simplify AndCondition in case of 0 or 1 conditions
def make_and_condition(conditions: List[Condition]):
    if len(conditions) == 0:
        return ConstantCondition(True)
    if len(conditions) == 1:
        return conditions[0]
    else:
        return AndCondition(conditions)


# Helper function to simplify OrCondition in case of 0 or 1 conditions
def make_or_condition(conditions: List[Condition]):
    if len(conditions) == 0:
        return ConstantCondition(False)
    if len(conditions) == 1:
        return conditions[0]
    else:
        return OrCondition(conditions)


@dataclass
class Link:
    from_index: int  # index in SMJsonData.node_list
    to_index: int  # index in SMJsonData.node_list
    cond: Condition


class SMJsonData:
    def __init__(self, sm_json_data_path):
        items_json = json.load(open(f'{sm_json_data_path}/items.json', 'r'))
        item_categories = ['implicitItems', 'upgradeItems', 'expansionItems']
        self.item_set = set(x if isinstance(x, str) else x['name'] for c in item_categories for x in items_json[c])
        self.flags_set = set(items_json['gameFlags'])
        self.helpers = {}

        tech_json = json.load(open(f'{sm_json_data_path}/tech.json', 'r'))
        self.tech_json_dict = {tech['name']: tech for tech in tech_json['techs']}
        self.tech_name_set = set(self.tech_json_dict.keys())
        helpers_json = json.load(open(f'{sm_json_data_path}/helpers.json', 'r'))
        self.helpers_json_dict = {helper['name']: helper for helper in helpers_json['helpers']}
        self.cond_dict = {}

        for tech_name in self.tech_json_dict.keys():
            self.register_tech_condition(tech_name)

        for helper_name in self.helpers_json_dict.keys():
            self.register_helper_condition(helper_name)

        self.node_list = []
        self.node_dict = {}
        self.node_ptr_list = []
        self.item_index_list = []
        self.link_list = []
        region_files = [str(f) for f in pathlib.Path(f"{sm_json_data_path}/region").glob("**/*.json")]
        for filename in region_files:
            # logging.info("Processing {}".format(filename))
            if "ceres" not in filename:
                region_data = json.load(open(filename, 'r'))
                self.process_region(region_data)
        # Add Pants Room in-room transition
        from_index = self.node_dict[(220, 2)]  # Pants Room
        to_index = self.node_dict[(322, 1)]  # East Pants Room
        self.link_list.append(Link(from_index, to_index, ConstantCondition(True)))

        self.door_ptr_pair_dict = {}
        connection_files = [str(f) for f in pathlib.Path(f"{sm_json_data_path}/connection").glob("**/*.json")]
        for filename in connection_files:
            connection_data = json.load(open(filename, 'r'))
            self.process_connections(connection_data)

    def register_tech_condition(self, name):
        if name in self.cond_dict:
            if self.cond_dict[name] is None:
                raise RuntimeError(f"Circular dependency in {name}")
        self.cond_dict[name] = None  # Set a sentinel value for detecting potential circular dependencies
        conds = [self.make_condition(c) for c in self.tech_json_dict[name]['requires']]
        self.cond_dict[name] = make_and_condition([TechCondition(name), *conds])

    def register_helper_condition(self, name):
        if name in self.cond_dict:
            if self.cond_dict[name] is None:
                raise RuntimeError(f"Circular dependency in {name}")
        self.cond_dict[name] = None  # Set a sentinel value for detecting potential circular dependencies
        self.cond_dict[name] = self.make_condition(self.helpers_json_dict[name]['requires'])

    def make_condition(self, json_data):
        if isinstance(json_data, str):
            if json_data == 'never':
                return ConstantCondition(True)  # Should be False but then we'd have to deal with obstacles better
            if json_data in self.item_set:
                return ItemCondition(json_data)
            if json_data in self.flags_set:
                return FlagCondition(json_data)
            if json_data in self.cond_dict.keys():
                return self.cond_dict[json_data]
            if json_data in self.tech_json_dict.keys():
                self.register_tech_condition(json_data)
                return self.cond_dict[json_data]
            if json_data in self.helpers_json_dict.keys():
                self.register_helper_condition(json_data)
                return self.cond_dict[json_data]
        elif isinstance(json_data, list):
            return make_and_condition([self.make_condition(x) for x in json_data])
        elif isinstance(json_data, dict):
            assert len(json_data) == 1
            key = next(iter(json_data.keys()))
            val = json_data[key]
            if key == 'or':
                return make_or_condition([self.make_condition(x) for x in val])
            if key == 'and':
                return make_and_condition([self.make_condition(x) for x in val])
            if key == 'ammo':
                # For now we ignore ammo quantity, just require one of the ammo type
                item_type = val['type']
                assert item_type in self.item_set
                return ItemCondition(item_type)
            if key == 'canShineCharge':
                return ShineChargeCondition(val['usedTiles'])
            if key == 'heatFrames':
                # For now we keep canHeatRun=False, so heat frames are irrelevant.
                return ConstantCondition(True)
            if key in ('lavaFrames', 'lavaPhysicsFrames', 'acidFrames', 'enemyDamage', 'spikeHits', 'hibashiHits', 'energyAtMost'):
                # For now we ignore energy requirements.
                return ConstantCondition(True)
            if key in ('enemyKill', 'resetRoom', 'previousStratProperty', 'previousNode'):
                # For now assume we can do these.
                return ConstantCondition(True)
            if key in ('canComeInCharged', 'adjacentRunway'):
                # For now assume we can't do these.
                return ConstantCondition(False)
        raise RuntimeError("Unrecognized condition: {}".format(json_data))

    def process_region(self, json_data):
        for room_json in json_data['rooms']:
            room_id = room_json['id']
            for node_json in room_json['nodes']:
                pair = (room_id, node_json["id"])
                self.node_dict[pair] = len(self.node_list)
                if 'nodeAddress' in node_json:
                    node_ptr = int(node_json['nodeAddress'], 16)
                    # Convert East Pants Room door pointers to corresponding Pants Room pointers
                    if node_ptr == 0x1A7BC:
                        node_ptr = 0x1A798
                    if node_ptr == 0x1A7B0:
                        node_ptr = 0x1A7A4
                else:
                    node_ptr = None
                if node_json['nodeType'] == 'item':
                    self.item_index_list.append(len(self.node_list))
                self.node_ptr_list.append(node_ptr)
                self.node_list.append(pair)
            for node_json in room_json['nodes']:
                if 'spawnAt' in node_json:
                    from_index = self.node_dict[(room_id, node_json['id'])]
                    to_index = self.node_dict[(room_id, node_json['spawnAt'])]
                    self.link_list.append(Link(from_index, to_index, ConstantCondition(True)))
            for link_json in room_json['links']:
                for link_to_json in link_json['to']:
                    strats = []
                    for strat_json in link_to_json['strats']:
                        requires = strat_json['requires']
                        if "obstacles" in strat_json:
                            for obstacle in strat_json['obstacles']:
                                requires = requires + obstacle['requires']
                        strats.append(self.make_condition(requires))
                    from_id = link_json['from']
                    from_index = self.node_dict[(room_id, from_id)]
                    to_id = link_to_json['id']
                    to_index = self.node_dict[(room_id, to_id)]
                    cond = make_or_condition(strats)
                    # if room_id == 181:
                    #     print(from_id, to_id, cond)
                    self.link_list.append(Link(from_index, to_index, cond))

    def process_connections(self, json_data):
        for connection in json_data['connections']:
            assert len(connection['nodes']) == 2
            src_pair = (connection['nodes'][0]['roomid'], connection['nodes'][0]['nodeid'])
            dst_pair = (connection['nodes'][1]['roomid'], connection['nodes'][1]['nodeid'])
            src_index = self.node_dict.get(src_pair)
            dst_index = self.node_dict.get(dst_pair)
            src_ptr = self.node_ptr_list[src_index] if src_index is not None else None
            dst_ptr = self.node_ptr_list[dst_index] if dst_index is not None else None
            if src_ptr is not None or dst_ptr is not None:
                self.door_ptr_pair_dict[(src_ptr, dst_ptr)] = src_index
                self.door_ptr_pair_dict[(dst_ptr, src_ptr)] = dst_index

#
# sm_json_data_path = "sm-json-data/"
# sm_json_data = SMJsonData(sm_json_data_path)
