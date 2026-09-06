"""seeing — one synchronized DSE participant-side market-seeing engine.

Pipeline (each stage is a module, each module is executable on its own):

    source adapters          seeing.capture.adapters.*
    → raw immutable capture  seeing.capture.raw_store
    → timestamp / sequence   seeing.normalize
    → book reconstruction    seeing.reconstruct.book
    → tape reconstruction    seeing.reconstruct.tape
    → event / queue          seeing.reconstruct.events
    → source fusion          seeing.fusion.fuse
    → one market state       seeing.fusion.state
    → microstructure         seeing.features.micro
    → state machine          seeing.state_machine.machine
    → experiment             seeing.experiment.run_experiment
    → falsification          seeing.experiment.falsify
    → KEEP / KILL / BLOCKED  seeing.experiment.verdict

Three truth classes are carried on every field (seeing.truth): OBSERVED,
INFERRED, NOT_OBSERVABLE. No field is ever invented; when a source lacks a
field the field is NOT_OBSERVABLE for that source and the engine keeps going.
"""

__version__ = "0.1.0"
