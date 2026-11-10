"""Independent health probes for local inference services."""
import json
import re
import subprocess
import threading
import time
import urllib.request

from catalog_dual_review import LOCALAI, LOCALAI_MODEL


_USAGE_LOCK=threading.Lock()
_USAGE_CACHE={'at':-1.0,'value':{'ollama':None,'localai':None}}


def _percent(text):
    """Parse one or more numeric samples without accepting arbitrary output."""
    values=[]
    for line in str(text).splitlines():
        match=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*',line)
        if match:values.append(float(match.group(1).replace(',','.')))
    return None if not values else max(0,min(100,round(max(values))))


def nvidia_usage(runner=subprocess.run):
    """Read the NVIDIA adapter load assigned to Ollama."""
    try:
        result=runner(['nvidia-smi.exe','--query-gpu=utilization.gpu',
                       '--format=csv,noheader,nounits'],capture_output=True,text=True,
                      timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return _percent(result.stdout) if result.returncode==0 else None
    except (OSError,subprocess.SubprocessError):return None


def localai_usage(runner=subprocess.run):
    """Read the busiest GPU engine owned by WSL's LocalAI virtual machine."""
    script=(
        "$pids=@(Get-Process -Name vmmemWSL,vmwp -ErrorAction SilentlyContinue).Id;"
        "if(-not $pids){exit 3};"
        "$samples=(Get-Counter '\\GPU Engine(*)\\Utilization Percentage' "
        "-MaxSamples 1 -ErrorAction Stop).CounterSamples;"
        "$values=@($samples|Where-Object{$m=[regex]::Match($_.Path,'pid_(\\d+)_');"
        "$m.Success -and $pids -contains [int]$m.Groups[1].Value -and "
        "$_.Path -match 'engtype_(compute|high priority compute)'}|"
        "ForEach-Object{$_.CookedValue});"
        "if($values.Count -eq 0){'0'}else{"
        "[Math]::Min(100,[Math]::Round(($values|Measure-Object -Maximum).Maximum))}")
    try:
        result=runner(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                      capture_output=True,text=True,timeout=5,
                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return _percent(result.stdout) if result.returncode==0 else None
    except (OSError,subprocess.SubprocessError):return None


def worker_gpu_usage(active=False, runner=subprocess.run, clock=time.monotonic):
    """Cache real adapter samples; poll more often while AVC is executing a job."""
    now=clock();ttl=2 if active else 12
    with _USAGE_LOCK:
        if _USAGE_CACHE['at']>=0 and now-_USAGE_CACHE['at']<ttl:
            return dict(_USAGE_CACHE['value'])
        value={'ollama':nvidia_usage(runner),'localai':localai_usage(runner)}
        _USAGE_CACHE.update(at=now,value=value)
        return dict(value)


def localai_status(opener=None, timeout=4):
    """Report reachability and configured-model availability without raising."""
    open_url=opener or urllib.request.urlopen
    try:
        with open_url(LOCALAI+'/v1/models',timeout=timeout) as response:
            data=json.load(response)
        models=[item.get('id','') for item in data.get('data',[]) if isinstance(item,dict)]
        return {'ok':True,'model':LOCALAI_MODEL,'model_ready':LOCALAI_MODEL in models}
    except Exception as exc:
        return {'ok':False,'model':LOCALAI_MODEL,'model_ready':False,'error':str(exc)}
