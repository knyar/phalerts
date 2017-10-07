[![Build Status](https://secure.travis-ci.org/knyar/phalerts.svg?branch=master)](http://travis-ci.org/knyar/phalerts?branch=master)
[![Coverage Status](https://coveralls.io/repos/github/knyar/phalerts/badge.svg?branch=master)](https://coveralls.io/github/knyar/phalerts?branch=master)

## Phabricator webhook for Prometheus Alertmanager

`phalerts` is a simple service that implements
[Alertmanager](https://github.com/prometheus/alertmanager) webhook receiver API
and creates/updates [Phabricator](https://www.phacility.com/phabricator/) tasks
based on alert notifications from Alertmanager.

The service:

* determines task title and description based on notification fields;
* if there is an existing open task with a given title, updates its description
  if necessary;
* if there is no open task, created a new one.

You can optionally provide a project name (or several) and `phalerts` will only
look for tasks in that project, and will assign that project to all new tasks it
creates.

## Usage

You will need to create a
[bot account](https://secure.phabricator.com/book/phabricator/article/users/#bot-accounts)
which will be used to manage alert-based tasks and generate an API token via
`[Bot User] > Manage > Edit Settings > Conduit API Tokens`.

Run `phalerts` like this:

```
PHABRICATOR_TOKEN=api-xxxxxx phalerts.py https://phab.company.tld bot-username
```

Then configure a new receiver in Alertmanager configuration file. For example,
to create/update tasks in the `prometheus-alerts` Phabricator project, define:

```yaml
receivers:
- name: phalerts
  webhook_configs:
  - url: http://localhost:8292/alerts?project=prometheus-alerts
    send_resolved: false
```

Finally, actually route some alerts to the receiver you've created.

## License

Licensed under MIT license.
