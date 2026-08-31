#!/usr/bin/env bash
set -euo pipefail

PROFILE=${PROFILE:-Rustatian}
REGION=${REGION:-eu-central-1}
TAGF=Name=tag:Project,Values=rapira-bench

awsx() {
  command aws --profile "$PROFILE" --region "$REGION" "$@"
}

ids=$(awsx ec2 describe-instances --filters "$TAGF" \
  Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped \
  --query 'Reservations[].Instances[].InstanceId' --output text)
sgs=$(awsx ec2 describe-security-groups --filters "$TAGF" --query 'SecurityGroups[].GroupId' --output text)
pgs=$(awsx ec2 describe-placement-groups --filters "$TAGF" --query 'PlacementGroups[].GroupName' --output text)
keys=$(awsx ec2 describe-key-pairs --filters "$TAGF" --query 'KeyPairs[].KeyName' --output text)

echo "nuke will delete (tag Project=rapira-bench only):"
echo "  instances: ${ids:-none}"
echo "  security groups: ${sgs:-none}"
echo "  placement groups: ${pgs:-none}"
echo "  key pairs: ${keys:-none}"

if [ -n "$ids" ]; then
  # shellcheck disable=SC2086
  awsx ec2 terminate-instances --instance-ids $ids >/dev/null
  echo "waiting for termination"
  # shellcheck disable=SC2086
  awsx ec2 wait instance-terminated --instance-ids $ids
fi

err=$(mktemp)
trap 'rm -f "$err"' EXIT
rc=0

for g in $sgs; do
  ok=0
  for _ in $(seq 1 18); do
    if awsx ec2 delete-security-group --group-id "$g" 2>"$err"; then
      echo "deleted security group $g"
      ok=1
      break
    fi
    grep -q 'NotFound' "$err" && { ok=1; break; }
    echo "  $g still in use; retrying in 10s"
    sleep 10
  done
  [ "$ok" = 1 ] || { echo "ERROR: could not delete security group $g; rerun make nuke"; rc=1; }
done

for p in $pgs; do
  ok=0
  for _ in $(seq 1 6); do
    if awsx ec2 delete-placement-group --group-name "$p" 2>"$err"; then
      echo "deleted placement group $p"
      ok=1
      break
    fi
    grep -q 'Unknown' "$err" && { ok=1; break; }
    sleep 10
  done
  [ "$ok" = 1 ] || { echo "ERROR: could not delete placement group $p; rerun make nuke"; rc=1; }
done

for k in $keys; do
  awsx ec2 delete-key-pair --key-name "$k" >/dev/null
  echo "deleted key pair $k"
done

echo "leftover sweep (reported, not deleted):"
vols=$(awsx ec2 describe-volumes --filters "$TAGF" Name=status,Values=available --query 'Volumes[].VolumeId' --output text)
enis=$(awsx ec2 describe-network-interfaces --filters "$TAGF" Name=status,Values=available --query 'NetworkInterfaces[].NetworkInterfaceId' --output text)
echo "  volumes: ${vols:-none}"
echo "  network interfaces: ${enis:-none}"
if [ "$rc" = 0 ]; then
  echo "nuke done; a later 'make up' recreates everything (run it after this, never before)"
else
  echo "nuke INCOMPLETE; see the errors above"
fi
exit "$rc"
