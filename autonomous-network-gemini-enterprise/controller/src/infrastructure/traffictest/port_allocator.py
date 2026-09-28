import logging

logger = logging.getLogger(__name__)

class PortAllocator:
    """
    Port allocator that manages individual ports using a bytearray for efficiency.
    The allocation state is shared at the class level across all instances.
    """
    PORT_OFFSET = 5200
    MAX_PORTS = 10000
    
    # Class-level bytearray where index i holds 1 if port (PORT_OFFSET + i) is busy, 0 otherwise
    _allocated_ports = bytearray(MAX_PORTS)

    def __init__(self):
        pass

    def alloc(self, number_of_ports, base_port=None):
        """
        Allocates 'number_of_ports' contiguous ports. 
        If base_port is specified, tries to allocate starting from that specific port.
        Otherwise finds the first available contiguous block of the requested size.
        """
        if number_of_ports <= 0:
            return []
        if number_of_ports > PortAllocator.MAX_PORTS:
            logger.error(f"Allocation request {number_of_ports} exceeds range {PortAllocator.MAX_PORTS}")
            return None

        if base_port:
            start_idx = self._get_idx(base_port)
            if self._is_range_free(start_idx, number_of_ports):
                return self._reserve_range(start_idx, number_of_ports)
            logger.warning(f"Requested base port {base_port} for {number_of_ports} ports is busy")
            return None
        else:
            start_idx = self._find_free_range(number_of_ports)
            if start_idx is not None:
                return self._reserve_range(start_idx, number_of_ports)
        
        logger.warning(f"No contiguous block of {number_of_ports} ports available")
        return None

    def mark_busy(self, ports):
        """Used for synchronization on startup"""
        for port in self._to_list(ports):
            idx = self._get_idx(port)
            if self._is_valid_idx(idx):
                self._set_busy(idx, True)
                logger.debug(f"Marked port {port} as busy")

    def free(self, ports):
        """Frees ports and marks them as available"""
        for port in self._to_list(ports):
            idx = self._get_idx(port)
            if self._is_valid_idx(idx):
                if self._is_busy(idx):
                    self._set_busy(idx, False)
                    logger.debug(f"Freed port {port}")
                else:
                    logger.warning(f"Port {port} was already free")
            else:
                logger.error(f"Port {port} is out of range")

    def is_free(self, port):
        """Public check for a single port"""
        idx = self._get_idx(port)
        return self._is_valid_idx(idx) and not self._is_busy(idx)

    # --- Internal Helpers ---

    def _is_busy(self, idx):
        return PortAllocator._allocated_ports[idx] == 1

    def _set_busy(self, idx, busy=True):
        PortAllocator._allocated_ports[idx] = 1 if busy else 0

    def _get_idx(self, port):
        return port - PortAllocator.PORT_OFFSET

    def _is_valid_idx(self, idx):
        return 0 <= idx < PortAllocator.MAX_PORTS

    def _to_list(self, val):
        return val if isinstance(val, list) else [val]

    def _is_range_free(self, start_idx, count):
        if not self._is_valid_idx(start_idx) or not self._is_valid_idx(start_idx + count - 1):
            return False
        return not any(self._is_busy(i) for i in range(start_idx, start_idx + count))

    def _find_free_range(self, count):
        for i in range(PortAllocator.MAX_PORTS - count + 1):
            if self._is_range_free(i, count):
                return i
        return None

    def _reserve_range(self, start_idx, count):
        ports = []
        for i in range(start_idx, start_idx + count):
            self._set_busy(i, True)
            ports.append(PortAllocator.PORT_OFFSET + i)
        logger.info(f"Allocated {count} ports starting at {ports[0]}")
        return ports
