# core/intelligence/hardware.py
# Hardware manager for MARK - CPU, GPU, RAM, VRAM monitoring and resource balancing

from __future__ import annotations

from typing import Dict, Any, Optional, NamedTuple
import threading
import time

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import pynvml
    pynvml.nvmlInit()
    HAS_NVML = True
    _nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:
    HAS_NVML = False
    pynvml = None


class ResourceSpec(NamedTuple):
    """Resource specifications for model routing."""
    cpu_percent: float
    cpu_cores: int
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    vram_percent: Optional[float] = None
    vram_used_mb: Optional[int] = None
    vram_total_mb: Optional[int] = None
    gpu_name: str = ""


class HardwareInfo(NamedTuple):
    """Hardware information snapshot."""
    spec: ResourceSpec
    timestamp: float
    max_ram_gb: float
    max_vram_mb: Optional[int]
    is_laptop: bool


def get_resource_spec() -> ResourceSpec:
    """Get current resource utilization specification."""
    if not HAS_PSUTIL:
        return ResourceSpec(
            cpu_percent=0.0,
            cpu_cores=0,
            ram_percent=0.0,
            ram_used_gb=0.0,
            ram_total_gb=1.0,
            vram_percent=None,
            vram_used_mb=None,
            vram_total_mb=None,
            gpu_name="",
        )
    
    # CPU info
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_cores = psutil.cpu_count(logical=True) or 1
    
    # RAM info
    vm = psutil.virtual_memory()
    ram_percent = vm.percent
    ram_used_gb = vm.used / (1024 ** 3)
    ram_total_gb = vm.total / (1024 ** 3)
    
    # GPU info
    vram_percent = None
    vram_used_mb = None
    vram_total_mb = None
    gpu_name = ""
    
    if HAS_NVML and pynvml is not None:
        try:
            info = pynvml.nvmlDeviceGetProperties(_nvml_handle)
            gpu_name = info.name.decode() if isinstance(info.name, bytes) else info.name
        except Exception:
            pass
        
        try:
            if pynvml is not None:
                vram_total = pynvml.nvmlDeviceGetTotalMemory(_nvml_handle)
                vram_used = pynvml.nvmlDeviceGetUsedMemory(_nvml_handle)
                vram_total_mb = int(vram_total / (1024 ** 2))
                vram_used_mb = int(vram_used / (1024 ** 2))
                if vram_total_mb > 0:
                    vram_percent = round((vram_used / vram_total) * 100, 1)
        except Exception:
            pass
    
    return ResourceSpec(
        cpu_percent=cpu_percent,
        cpu_cores=cpu_cores,
        ram_percent=ram_percent,
        ram_used_gb=round(ram_used_gb, 1),
        ram_total_gb=round(ram_total_gb, 1),
        vram_percent=vram_percent,
        vram_used_mb=vram_used_mb,
        vram_total_mb=vram_total_mb,
        gpu_name=gpu_name,
    )


def get_hardware_info() -> HardwareInfo:
    """Get complete hardware information."""
    spec = get_resource_spec()
    
    # Determine if laptop
    is_laptop = False
    try:
        import wmi
        wmi_obj = wmi.WMI(namespace="root/cimv2")
        for system in wmi_obj.Win32_ComputerSystem():
            model = system.Model or ""
            manufacturer = system.Manufacturer or ""
            is_laptop = "laptop" in model.lower() or "notebook" in model.lower() or \
                       "portable" in model.lower() or manufacturer.lower().startswith("portable")
    except Exception:
        pass
    
    return HardwareInfo(
        spec=spec,
        timestamp=time.time(),
        max_ram_gb=spec.ram_total_gb,
        max_vram_mb=spec.vram_total_mb,
        is_laptop=is_laptop,
    )


class ResourceBalancer:
    """Balances resource allocation based on available hardware."""
    
    # Thresholds for resource-aware decision making
    CPU_LOW_THRESHOLD = 30.0    # % - consider lightweight operations
    CPU_HIGH_THRESHOLD = 70.0   # % - avoid heavy computation
    RAM_LOW_THRESHOLD = 40.0    # % GB - allow moderate context
    RAM_HIGH_THRESHOLD = 80.0   # % GB - compress context
    VRAM_LOW_THRESHOLD = 30.0   # % - use smaller models
    VRAM_HIGH_THRESHOLD = 70.0  # % - prefer larger models or cloud
    
    def __init__(self) -> None:
        self._last_update = 0.0
        self._update_interval = 2.0  # seconds
    
    def should_use_efficient_mode(self) -> bool:
        """Determine if we should use efficient (lighter) model mode."""
        spec = get_resource_spec()
        
        # If CPU is heavily loaded, use efficient mode
        if spec.cpu_percent > self.CPU_HIGH_THRESHOLD:
            return True
        
        # If RAM is low, use efficient mode
        if spec.ram_percent < self.RAM_LOW_THRESHOLD:
            return True
        
        # If VRAM is low, use efficient mode
        if spec.vram_percent is not None and spec.vram_percent < self.VRAM_LOW_THRESHOLD:
            return True
        
        return False
    
    def should_use_cloud_mode(self) -> bool:
        """Determine if we should use cloud AI mode."""
        spec = get_resource_spec()
        
        # If VRAM is very low (no GPU), use cloud
        if spec.vram_percent is None or spec.vram_percent < self.VRAM_LOW_THRESHOLD:
            # Check if we have any VRAM at all
            if spec.vram_total_mb is None or spec.vram_total_mb < 1024:
                return True
        
        # If RAM is very low, consider cloud for heavy tasks
        if spec.ram_percent > self.RAM_HIGH_THRESHOLD:
            return True
        
        return False
    
    def get_context_budget(self, base_budget: int) -> int:
        """Adjust context window budget based on available resources."""
        spec = get_resource_spec()
        
        budget = base_budget
        
        # Reduce context if RAM is low
        if spec.ram_percent < self.RAM_LOW_THRESHOLD:
            budget = int(budget * 0.5)
        elif spec.ram_percent < self.RAM_HIGH_THRESHOLD:
            budget = int(budget * 0.75)
        
        # Reduce context if VRAM is low
        if spec.vram_percent is not None and spec.vram_percent < self.VRAM_LOW_THRESHOLD:
            budget = int(budget * 0.5)
        elif spec.vram_percent is not None and spec.vram_percent < self.VRAM_HIGH_THRESHOLD:
            budget = int(budget * 0.75)
        
        # Increase context if we have plenty of resources
        if spec.ram_percent < 30.0 and (spec.vram_percent is None or spec.vram_percent > 70.0):
            budget = int(budget * 1.25)
        
        return max(256, min(8192, budget))
    
    def get_model_preference(self) -> str:
        """Get preferred model size category based on available resources."""
        spec = get_resource_spec()
        
        has_gpu = spec.vram_total_mb is not None and spec.vram_total_mb >= 2048
        has_good_ram = spec.ram_total_gb >= 16.0
        
        if has_gpu and spec.vram_percent is not None and spec.vram_percent > 50.0:
            if has_good_ram:
                return "large"      # Full-size model with good GPU + RAM
            return "medium"        # Medium model with good GPU
        elif has_gpu and spec.vram_percent is not None and spec.vram_percent > 20.0:
            return "medium"
        elif has_gpu:
            return "small"         # Small model with limited GPU
        elif has_good_ram:
            return "medium"        # Medium model with good RAM, no GPU
        else:
            return "efficient"     # Efficient/small model, CPU-only