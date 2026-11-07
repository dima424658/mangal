# Source from Bash. Prefer an already active OpenCFD v2606 environment.
if [ "${WM_PROJECT_VERSION:-}" != v2606 ] && [ "${WM_PROJECT_VERSION:-}" != 2606 ]; then
    if [ -f /Volumes/OpenFOAM-v2606/etc/bashrc ]; then
        source /Volumes/OpenFOAM-v2606/etc/bashrc
    elif [ -f /usr/lib/openfoam/openfoam2606/etc/bashrc ]; then
        source /usr/lib/openfoam/openfoam2606/etc/bashrc
    elif [ -n "${OPENFOAM_BASHRC:-}" ]; then
        source "$OPENFOAM_BASHRC"
    else
        echo 'Source OpenCFD OpenFOAM v2606 etc/bashrc or set OPENFOAM_BASHRC.' >&2
        exit 1
    fi
fi
