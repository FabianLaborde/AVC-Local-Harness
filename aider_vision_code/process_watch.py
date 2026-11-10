"""Bounded live output monitor; Windows communicate(timeout) hides partial output."""
import queue
import subprocess
import threading
import time


def watch_process(proc, cancel, timeout, stop_markers, terminate, log, audit):
    messages = queue.Queue()

    def reader():
        try:
            for line in proc.stdout:
                messages.put(line)
        finally:
            proc.stdout.close()
            messages.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    start = time.monotonic()
    notice = start + 30
    output = ''
    ended = False
    while True:
        try:
            line = messages.get(timeout=.1)
            if line is None:
                ended = True
            else:
                output = (output + line)[-64000:]
        except queue.Empty:
            pass
        elapsed = time.monotonic()-start
        marker = next((m for m in stop_markers if m in output), None)
        if cancel.is_set() or elapsed >= timeout or marker:
            terminate(proc)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            thread.join(timeout=1)
            while not messages.empty():
                line = messages.get_nowait()
                if line:
                    output = (output + line)[-64000:]
            code = -1 if cancel.is_set() else -3 if marker else -2
            reason = ('Detenido por el usuario.' if code == -1 else
                      'Reintento interno detenido por fallo de formato.' if code == -3 else 'Tiempo de espera agotado.')
            audit('process_stopped', cancelled=code == -1, elapsed_seconds=round(elapsed),
                  timeout=timeout, reason='edit_format' if marker else 'cancelled' if code == -1 else 'timeout')
            return code, output+'\n'+reason
        if ended and proc.poll() is not None:
            return proc.returncode, output
        if time.monotonic() >= notice:
            log(f'Proceso en ejecución: {round(elapsed)} s (límite {round(timeout)} s). Esperando salida del editor.')
            notice = time.monotonic()+30
