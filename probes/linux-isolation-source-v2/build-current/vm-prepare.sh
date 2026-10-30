#!/bin/sh
set -eu

boot_id=${1:?boot id required}
cmdline=" $(cat /proc/cmdline) "
if ! grep -q '^PPid:[[:space:]]*1$' "/proc/$$/status"; then
    echo "PREPARE_REJECTED parent-not-pid1" >&2
    exit 1
fi
case "$cmdline" in *" rdinit=/init "*) ;; *) echo "PREPARE_REJECTED rdinit" >&2; exit 1 ;; esac

root=/sys/fs/cgroup
[ -f "$root/cgroup.controllers" ] || { echo "PREPARE_REJECTED no-cgroup2" >&2; exit 1; }
for controller in cpu memory pids; do
    grep -qw "$controller" "$root/cgroup.controllers" || {
        echo "PREPARE_REJECTED controller=$controller" >&2
        exit 1
    }
done
printf '%s\n' '+cpu +memory +pids' > "$root/cgroup.subtree_control"
mkdir -p "$root/mingos" /run/mingos-workers
chmod 700 "$root/mingos" /run/mingos-workers
printf '%s\n' '+cpu +memory +pids' > "$root/mingos/cgroup.subtree_control"

echo "PREPARED_OWNED_ROOTS boot_id=$boot_id profile=source-v2"
