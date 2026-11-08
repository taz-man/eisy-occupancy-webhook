"""Occupancy location node: In when any member device is In."""
import udi_interface

Node = udi_interface.Node


class OccupancyLocation(Node):
    id = 'occloc'
    drivers = [{'driver': 'ST', 'value': 0, 'uom': 2}]

    def set_occupied(self, occupied):
        from occ_device import apply_state
        return apply_state(self, occupied)
