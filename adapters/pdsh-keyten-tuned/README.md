# Experimental tuned Keyten overlays

These files preserve manually rewritten PDS-H plans for diagnostic experiments.
They are not used by `runner/run_pdsh.sh`, are not part of the standard adapter,
and must never be presented as the headline cross-engine result.

To run an experiment, copy an individual query over a disposable harness
snapshot and record the overlay digest and `adapter_track=tuned` explicitly.
