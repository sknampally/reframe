"""Network clean-up and the final leftover check (specification section 4, stage 9).

AWS Transform leaves a detached network interface in the graph's VPC after the job ("Elastic network
interface managed by AWS AWSTransform service", status available). It blocks the security group and
subnet from being deleted, and the Terraform provider stopped responding while waiting on them (spike 1).
Only interfaces in the run's own VPC, from Terraform's state, are ever deleted.

AWS's tag index keeps entries for a while after deletion, so each entry it lists for the run is checked
against its own service; only one that still exists counts as left over.
"""

from __future__ import annotations

import functools
import time
from typing import Any

from reframe.core.errors import NotSent, Outcome, Stop
from reframe.core.runfolder import Changes
from reframe.core.shell import Aws

TRANSFORM_ENI = "Elastic network interface managed by AWS AWSTransform service."


def transform_interfaces(aws: Aws, vpc_id: str) -> list[dict[str, Any]]:
    reply = aws.read("ec2", "describe-network-interfaces", "--filters", f"Name=vpc-id,Values={vpc_id}",
                     "Name=status,Values=available", f"Name=description,Values={TRANSFORM_ENI}")
    return [{"id": i["NetworkInterfaceId"], "description": i.get("Description")} for i in reply.get("NetworkInterfaces") or []]


def delete_interfaces(aws: Aws, changes: Changes, vpc_id: str) -> list[str]:
    deleted = []
    for interface in transform_interfaces(aws, vpc_id):
        changes.apply(f"teardown-eni-{interface['id']}", "ec2 delete-network-interface", {"interface": interface["id"], "vpc": vpc_id},
                      functools.partial(aws.write, "ec2", "delete-network-interface", "--network-interface-id", interface["id"]))
        deleted.append(interface["id"])
    return deleted


def wait_for_lambda_interfaces(aws: Aws, vpc_id: str, function: str, timeout: float = 2700, poll: float = 30,
                               sleep=time.sleep, clock=time.time) -> int:
    """After the graph loader is deleted, AWS keeps its network interfaces for a while (spike P2-02) and the subnets and
    security groups cannot be deleted until they go. Waits until none is left; returns the seconds waited."""
    started = clock()
    while True:
        reply = aws.read("ec2", "describe-network-interfaces", "--filters", f"Name=vpc-id,Values={vpc_id}",
                         "Name=interface-type,Values=lambda", f"Name=description,Values=AWS Lambda VPC ENI-{function}*")
        if not reply.get("NetworkInterfaces"):
            return int(clock() - started)
        if clock() - started > timeout:
            raise Stop(Outcome.WAITING_HUMAN, f"AWS has not released the graph loader's network interfaces after {timeout // 60:.0f} minutes; "
                                              "run reframe teardown again later")
        sleep(poll)


def tagged(aws: Aws, run: str, project: str) -> list[str]:
    arns, token = [], None
    while True:
        arguments = ["resourcegroupstaggingapi", "get-resources", "--tag-filters", f"Key=ReframeRun,Values={run}",
                     f"Key=ReframeProject,Values={project}"]
        if token:
            arguments += ["--pagination-token", token]
        page = aws.read(*arguments)
        arns += [item["ResourceARN"] for item in page.get("ResourceTagMappingList") or []]
        token = page.get("PaginationToken")
        if not token:
            return arns


EC2_DESCRIBE = {"vpc": ("describe-vpcs", "--vpc-ids"), "subnet": ("describe-subnets", "--subnet-ids"),
                "security-group": ("describe-security-groups", "--group-ids"),
                "security-group-rule": ("describe-security-group-rules", "--security-group-rule-ids"),
                "vpc-endpoint": ("describe-vpc-endpoints", "--vpc-endpoint-ids"), "route-table": ("describe-route-tables", "--route-table-ids"),
                "network-interface": ("describe-network-interfaces", "--network-interface-ids")}


def still_exists(aws: Aws, arn: str) -> bool | None:
    """True or False from the resource's own service; None when Reframe has no check for its type."""
    parts = arn.split(":", 5)
    service, resource = parts[2], parts[5] if len(parts) > 5 else ""
    try:
        if service == "ec2":
            kind, _, ident = resource.partition("/")
            if kind not in EC2_DESCRIBE:
                return None
            command, flag = EC2_DESCRIBE[kind]
            aws.read("ec2", command, flag, ident)
            return True
        if service == "s3":
            aws.read("s3api", "head-bucket", "--bucket", resource)
            return True
        if service == "iam" and resource.startswith("role/"):
            aws.read("iam", "get-role", "--role-name", resource.rsplit("/", 1)[-1])
            return True
        if service == "lambda" and resource.startswith("function:"):
            aws.read("lambda", "get-function", "--function-name", resource.split(":", 1)[1])
            return True
        if service == "logs" and resource.startswith("log-group:"):
            name = resource.split(":", 1)[1].removesuffix(":*")
            found = aws.read("logs", "describe-log-groups", "--log-group-name-prefix", name).get("logGroups") or []
            return any(g.get("logGroupName") == name for g in found)
        if service == "rds" and resource.startswith("cluster:"):
            aws.read("neptune", "describe-db-clusters", "--db-cluster-identifier", resource.split(":", 1)[1])
            return True
        if service == "rds" and resource.startswith("db:"):
            aws.read("neptune", "describe-db-instances", "--db-instance-identifier", resource.split(":", 1)[1])
            return True
        if service == "rds" and resource.startswith("subgrp:"):
            aws.read("neptune", "describe-db-subnet-groups", "--db-subnet-group-name", resource.split(":", 1)[1])
            return True
    except NotSent as error:
        text = str(error)
        if any(marker in text for marker in ("NotFound", "(404)", "NoSuchEntity", "not found", "does not exist", "Not Found",
                                             "ResourceNotFoundException")):
            return False
        raise
    return None


def leftovers(aws: Aws, run: str, project: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {"exists": [], "stale_index": [], "unchecked": []}
    for arn in tagged(aws, run, project):
        state = still_exists(aws, arn)
        found["exists" if state else "stale_index" if state is False else "unchecked"].append(arn)
    return found
