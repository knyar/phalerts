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

You can optionally provide a project name/ID (or several) and `phalerts` will only
look for tasks in that project, and will assign that project to all new tasks it
creates.

## Usage

You will need to create a
[bot account](https://secure.phabricator.com/book/phabricator/article/users/#bot-accounts)
which will be used to manage alert-based tasks and generate an API token via
`[Bot User] > Manage > Edit Settings > Conduit API Tokens`.

`phalerts` expects the token to be present in `PHABRICATOR_TOKEN` environment
variable, so you'll typically use a command like this to start it:

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

At each call the Phabricator ID (`PHID`) for `prometheus-alerts` will be looked
up. Specifying IDs is also supported via the `phid` query string parameter.

Finally, actually route some alerts to the receiver you've created.

### Title template

Tasks created by `phalerts` will be titled by a jinja template, by default the
alert group's name (`--tpl_format` CLI option). You can override the title
template with the `title` query string parameter.

## Known issues

Search queries issued to Phabricator only process first 100 results. This is
unlikely to be a problem (especially for small installations), however you might
need to implement paging support in `phalerts` if you have many projects or
open tasks with similar names. You should see `phalerts_request_errors_total`
counter incremented and "Unexpected 'after' cursor" error messages if this
becomes a problem.

## License

Licensed under MIT license.
