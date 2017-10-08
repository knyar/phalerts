#!/usr/bin/env python3
"""Creates and updates Phabricator / Maniphest tasks for Prometheus alerts.

phalerts implements Prometheus alertmanager webhook API and creates/updates
Phabricator tasks for alerts. Inspired by https://github.com/fabxc/jiralerts

https://github.com/knyar/phalerts
Licensed under MIT license.
"""

import argparse
import logging
import os
import sys

from flask import Flask, request, make_response
from jinja2 import Template
from phabricator import Phabricator
import prometheus_client as prometheus

app = Flask(__name__)
args = None
phab = None

# Jinja2 template used for task description.
TPL_DESCRIPTION = r"""
== Common information

{% for k, v in commonAnnotations|dictsort -%}
* **{{ k }}**: {{ v }}
{% endfor %}
{% for k, v in commonLabels|dictsort -%}
* **{{ k }}**: {{ v }}
{% endfor %}

== Firing alerts
{% for a in alerts|sort(attribute='startsAt') if a.status == 'firing' -%}

---

  {% for k, v in a.annotations|dictsort -%}
* **{{ k }}**: {{ v }}
  {% endfor %}
  {% for k, v in a.labels|dictsort -%}
* **{{ k }}**: {{ v }}
  {% endfor -%}
* [Source]({{ a.generatorURL }})

{% endfor %}
"""
metric_request_latency = prometheus.Histogram(  # pylint: disable=no-value-for-parameter
    "phalerts_request_latency_seconds", "Latency of incoming requests")
metric_error_count = prometheus.Counter(  # pylint: disable=no-value-for-parameter
    "phalerts_request_errors_total", "Number of request processing errors")
metric_phabricator_latency = prometheus.Histogram(  # pylint: disable=no-value-for-parameter
    "phalerts_phabricator_latency_seconds",
    "Latency of outgoing Phabricator requests", ["api_call"])

class Error(RuntimeError):
    pass

def phab_request(api_func, **kwargs):
    """Sends a Phabricator API request, measuring latency and logging result."""
    api_call = "%s.%s" % (api_func.method, api_func.endpoint)
    with metric_phabricator_latency.labels(api_call).time():  # pylint: disable=no-member
        result = api_func(**kwargs)
        logging.debug("Got %s response: %s", api_call, result)
        return result

def find_project_phid(name):
    """Looks for a project with a given name in Phabricator.

    Returns phid of a single matched project, or raises an Error if project has
    not been found.

    Args:
        name: (string) name of a project.
    """
    result = phab_request(phab.project.search, constraints=dict(name=name))
    for project in result["data"]:
        # For the list of fields, see:
        # https://secure.phabricator.com/conduit/method/project.search/
        if project["fields"]["name"] == name:
            return project["phid"]
    raise Error("Could not find project %s" % name)

def create_task(title, description, projects):
    """Creates a Maniphest task.

    Tasks are created by using `maniphest.edit` API endpoint without an
    objectIdentifier. For more details, see:
    https://secure.phabricator.com/conduit/method/maniphest.edit/

    Args:
        title: (string) task title.
        description: (string) task description.
        projects: (list of strings) names of projects that a new task should be
            assigned to.
    """
    transactions = [
        dict(type="title", value=title),
        dict(type="description", value=description),
    ]
    if projects:
        project_phids = [find_project_phid(n) for n in projects]
        transactions.append(dict(type="projects.add", value=project_phids))
    result = phab_request(phab.maniphest.edit, transactions=transactions)
    if not result["object"]:
        raise Error("Failed to create task. Got response: %s" % result)
    logging.info("Created task %s/T%s", args.phabricator_url.rstrip('/'),
                 result["object"]["id"])

def update_task(task, description):
    """Updates description of an existing Maniphest task.

    Args:
        task: a nested dictionary corresponding to an existing Maniphest task
            (as returned by `find_task`).
        description: (string) new task rescription.
    """
    transactions = [dict(type="description", value=description)]
    result = phab_request(
        phab.maniphest.edit,
        transactions=transactions,
        objectIdentifier=task["phid"])
    if len(result["transactions"]) < len(transactions):
        raise Error("Failed to apply all transactions for task %s. Got %s" % (
            task["phid"], result))

def find_task(title, projects):
    """Looks for an open task with a given title in a given project.

    Returns a single matched task. For the list of fields, see:
    https://secure.phabricator.com/conduit/method/maniphest.search/

    Args:
        title: (string) task title.
        projects: (list of strings) names of projects that should be assigned to
            a task to be returned.
    """
    project_phids = set([find_project_phid(n) for n in projects])

    result = phab_request(
        phab.maniphest.search,
        constraints=dict(
            fulltext=title,
            statuses=["open"],
            projects=projects,
        ),
        attachments=dict(projects=True),
        # this expands to "title, id", so we'll get the newest open task if
        # multiple open tasks exist.
        order="title",
    )
    for task in result["data"]:
        # 'fulltext' constraint actually does a full text search, so we iterate
        # over returned tasks looking for a one with the title we need.
        if task["fields"]["name"] != title:
            continue
        task_projects = set(task["attachments"]["projects"]["projectPHIDs"])
        # Check that the task has all required projects assigned to it.
        if not project_phids.issubset(task_projects):
            continue
        return task

def process_task(title, description, projects):
    """Makes sure there is a task with a given title and description.

    Either creates or updates an existing task with a given description.

    Args:
        title: (string) task title.
        description: (string) task description.
        projects: (list of strings) names of projects that a task should be
            assigned to.
    """
    logging.info("Looking for tasks with title='%s' in %s", title, projects)
    task = find_task(title, projects)

    if not task:
        logging.info("Creating a task with title='%s' in %s", title, projects)
        create_task(title, description, projects)
    elif task["fields"]["description"]["raw"] == description:
        logging.info("Task %s/T%s already exists with correct description",
                     args.phabricator_url.rstrip('/'), task["id"])
    else:
        logging.info("Updating task %s/T%s",
                     args.phabricator_url.rstrip('/'), task["id"])
        update_task(task, description)

@app.route("/alerts", methods=["POST"])
@metric_request_latency.time()
def alerts():
    """Processes a POST request from Alertmanager."""
    unknown_args = set(request.args.keys()) - {"project"}
    if unknown_args:
        logging.error("Unexpected args %s", unknown_args)
        return "Unexpected args %s" % unknown_args, 400

    data = request.get_json()
    if data["version"] != "4":
        logging.error("Unknown message version %s", data["version"])
        return "Unknown message version %s" % data["version"], 400

    logging.debug("Got data: %s", data)

    title = Template(args.tpl_title).render(data)
    description = Template(TPL_DESCRIPTION).render(data)

    try:
        process_task(title, description, request.args.getlist("project"))
    except Error as e:
        logging.error("Got error: %s", e)
        metric_error_count.inc()
        return "Error", 500

    return "OK", 200

@app.route("/metrics")
def metrics():
    resp = make_response(prometheus.generate_latest(prometheus.core.REGISTRY))
    resp.headers["Content-Type"] = prometheus.CONTENT_TYPE_LATEST
    return resp, 200

def main():
    global args  # pylint: disable=global-statement
    global phab  # pylint: disable=global-statement
    parser = argparse.ArgumentParser(
        description="Creates and updates Maniphest tasks for alerts",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "-p", "--port", type=int, default=8292, help="Port to bind to")
    parser.add_argument(
        "-b", "--bind", default="localhost", help="Host to bind to")
    parser.add_argument(
        "-t", "--tpl_title", default="{{ groupLabels.alertname }}",
        help="Jinja2 template used for task title")
    parser.add_argument(
        "-d", "--debug", action="store_const", dest="loglevel",
        const=logging.DEBUG, default=logging.INFO, help="Enable debug logging")
    parser.add_argument(
        "phabricator_url",
        help="Base URL of Phabricator, e.g. https://phabricator.company.tld")
    parser.add_argument(
        "phabricator_user", help=(
            "Phabricator username. Authentication token should be provided via "
            "PHABRICATOR_TOKEN environment variable"))
    args = parser.parse_args()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s", level=args.loglevel)

    token = os.environ.get("PHABRICATOR_TOKEN")
    if not token:
        print("PHABRICATOR_TOKEN not set")
        sys.exit(1)

    phab = Phabricator(host="%s/api/" % args.phabricator_url.rstrip('/'),
                       username=args.phabricator_user, token=token)
    app.run(host=args.bind, port=args.port)

if __name__ == "__main__":
    main()
