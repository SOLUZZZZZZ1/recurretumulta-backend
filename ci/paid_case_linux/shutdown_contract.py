"""Strict evidence for the controlled Uvicorn 0.30.6 POSIX shutdown."""
import signal

def evaluate_shutdown(*,version,started,alive_before,requested_signal,returncode,port_closed,log,pid,timed_out=False,forced=False):
    lifecycle='Application shutdown complete.' in log and f'Finished server process [{pid}]' in log
    result={'version':version,'startup_verified':started,'alive_before_signal':alive_before,
            'requested_signal':requested_signal,'returncode':returncode,
            'lifespan_shutdown_logged':lifecycle,'port_closed':port_closed,'timed_out':timed_out,'forced':forced}
    result['passed']=bool(version=='0.30.6' and started and alive_before and requested_signal==signal.SIGTERM
        and returncode in (0,-signal.SIGTERM) and lifecycle and port_closed and not timed_out and not forced)
    return result
