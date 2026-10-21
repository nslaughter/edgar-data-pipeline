# Security and access

<!-- status: The controls; The charts; Fetcher; Scheduler; Discoverer; Garage; PostgreSQL 18; Monitoring: metrics, alerts, and dashboards; The snapshot store in git -->
**Status** ([status page](../status.md)): The controls: Specified. The charts: Built. Fetcher: Specified. Scheduler: Specified. Discoverer: Decided; specified in part. Garage: Built. PostgreSQL 18: Built. Monitoring: metrics, alerts, and dashboards: Decided; specified in part; built in part. The snapshot store in git: Built.
<!-- end status -->

Each credential the pipeline uses and where it's kept, how the user agent
that the SEC asks for is handled, who may change what, and what each service
may read and write. Facts about `edgar-asof` here cite its documents at
commit `515bb0d`, and were checked against its `main` on October 14, 2026.

## The credentials

A credential, as the deployment specification uses the word, is a password,
token, key, or shared secret by which one component proves itself to
another, or that guards an administrative interface
([deployment terms][deployment-terms]). Each component keeps its own in
Secrets ([credentials]).

| Credential | Used by | Where it comes from |
| --- | --- | --- |
| Garage's RPC secret, and its admin token | Garage's nodes; the setup Job, which reaches Garage's admin API with the token ([GARAGE-2]) | Required values of the umbrella chart ([SECRET-1]) |
| The ID and secret of each Garage key | The fetcher's key, which may read and write the store's bucket `snapshots`, and no other ([GARAGE-4]); the discoverer's key, which may only read `snapshots` ([GARAGE-4]); the key of `database-backups`, which may read and write only that bucket, for the database's backups ([GARAGE-2], [PG-4]); each other service's key, which comes with that service ([GARAGE-4]) | Required values ([SECRET-1]) |
| The password of each service's database role | In the deployment's version 1.1.0, the fetcher's role and the discoverer's; each other service's comes with that service ([PG-7]) | Required values ([SECRET-1]) |
| The password of Grafana's database role, `grafana` | Grafana's PostgreSQL data source, which reads it from the role's Secret ([PG-11], [MON-5]) | A required value ([SECRET-1]) |
| The database owner's password, of the role `edgar` | Only the Job that creates the record's tables and views and grants each role its privileges ([PG-6], [PG-8], [PG-12], [PG-13]) | Generated in the cluster by CloudNativePG, which keeps it there ([PG-6], [credentials]) |
| Grafana's admin password | Grafana | A required value ([SECRET-1]) |

Four rules hold them together:

- **Every credential but those an operator generates is a value of the
  umbrella chart that no committed file sets.** Its `values.schema.json`
  requires each one, so `helm template` fails without them ([SECRET-1]).
- **Nothing is generated as the charts render.** The same values render the
  same manifests, so no random credential appears in a render ([SECRET-2]).
- **A credential appears only in a Secret,** never in a ConfigMap, a
  container's arguments or literal environment values, or a custom
  resource's `spec` ([SECRET-3]).
- **CI generates new credentials for each cluster it creates. The owner
  keeps theirs in a values file git ignores,** and never puts one in a
  commit, a pull request, or a log ([SECRET-4]).

So no credential is committed, generated as the charts render, or shown
outside a Secret ([deployment guarantees][the deployment's guarantees]).
Grafana's PostgreSQL data source names its password as an environment
variable of Grafana's container, set from its role's Secret, so Grafana's
`datasources` value holds none ([MON-5]).

## The user agent

Every request to the SEC carries a `User-Agent`, the user's own, which must
contain an `@`, following the SEC's sample of a name and a contact address.
The repository ships none, and without one nothing is fetched ([FETCH-2]).
It isn't a credential in the deployment specification's sense, but it names
the owner, and no requester identity is stored
([store guarantees][store-guarantees]).

| Who sends requests | Where the user agent comes from |
| --- | --- |
| `scripts/snapshot.py` | The environment's `SEC_USER_AGENT`, or else `.env.dev`, a file at the repository's root that git ignores ([FETCH-15]) |
| The fetcher | `SEC_USER_AGENT` in its environment, set from one Secret that the chart renders from the value `fetcher.secUserAgent`. Only the fetcher's containers read it, and no committed values file sets it: the owner keeps it in a values file git ignores ([ID-2], [FETCH-17]) |
| `edgar-asof`, used on its own | An argument the caller passes; the library reads none from the environment or any file ([CLIENT-2]) |

In the cluster, only the active fetcher sends requests, and the services
that read EDGAR's content through `edgar-asof` give it bodies from the store
([FETCH-18]). The fetcher alone mounts the Secret that holds the user agent
([0005]), and sends nothing without one that contains an `@` ([FETCH-17]).

Nothing the pipeline keeps or sends elsewhere holds it. No file under
`data/`, no row of the cluster store, and no job or event contains the
`User-Agent`, or any other request header ([ID-1]), and no file in
`edgar-asof`'s cache does either ([CLIENT-3]). No metric's label carries the
user agent, a credential, or a request header ([METRIC-2]), and no span's
attributes or events do ([TRACE-5]). In the other direction, the fetcher
sends the SEC no trace context ([TRACE-6]).

## Who may change what

The owner sets each control, and the services report what they did with it.
Every control is a field of a custom resource's `spec`, `Source`,
`Backfill`, or `SecAccess`, rendered from the umbrella chart's values; the
services set only `status` ([the controls' guarantees], [VAL-1]). The owner
changes the resources only through the chart's values, with `helm install`
or `helm upgrade`, never with `kubectl` or another client, and never changes
their `status`: `kubectl` is for reading them ([RBAC-6]). In the cluster,
"only when the owner asks" becomes a permission ([0007]).

RBAC grants each ServiceAccount exactly this on the three resources
([who may change what]):

| ServiceAccount | Sources and Backfills | Their `status` | The `SecAccess` | Its `status` |
| --- | --- | --- | --- | --- |
| The scheduler's | `get`, `list`, `watch` | `update`, `patch` | `get`, `list`, `watch` | None |
| The fetcher's | None | None | `get`, `list`, `watch` | `update`, `patch` |
| kube-state-metrics' | `get`, `list`, `watch` | None | `get`, `list`, `watch` | None |

The chart grants each of these its row and no more ([RBAC-1], [RBAC-2],
[RBAC-3]). No other Role or ClusterRole in the charts, their dependencies'
included, grants any verb on the resources or their `status`, and none
grants `create`, `update`, `patch`, `delete`, or `deletecollection` on the
resources themselves ([RBAC-4]). On the cluster, the API server's access
reviews confirm that each ServiceAccount can do exactly what its row grants
([RBAC-5]). None of the services adds finalizers, labels, or annotations, so
none needs to change a resource itself ([who may change what]). The
discoverer has no row: it can't read Backfills, which is why the scheduler
hands each one to it in an order ([the notices' Backfills]).

The chart checks the owner's values before anything reaches the cluster. It
refuses values from which it would render a resource that the API server
would refuse, apart from a change to a Backfill's `spec`, which the chart
can't see without the cluster, and Sources not paused that together would
poll faster than half the fetcher's limit ([VAL-2], [VAL-3]). The API server
refuses a changed Backfill `spec`, so `helm upgrade` fails then, though
`helm template` passes ([API-3], [the chart's values][controls-values]). Its
default values pause the `SecAccess`, so an install sends nothing to the SEC
until the owner's values allow it ([VAL-4]). Nothing reaches the cluster
another way, so no admission webhook checks the resources; should anything,
the definitions still refuse a cadence under a second, and the scheduler
reports a total over the budget ([the chart's values][controls-values],
[0010]).

Two acts are the owner's alone, by design. Acknowledging a stop after a
`403`: the owner copies the stop's `at` into the values as
`controls.secAccess.acknowledgedStopAt`, and runs `helm upgrade`
([SecAccess][controls-secaccess]). And widening the scope: `data/scope.json`
in a commit of its own, before anything it adds is fetched or transferred
into the git store, and the cluster's through the chart's values
([the scope and the cluster][scope-cluster]).

Beyond the three resources:

- **The fetcher's Lease.** The chart grants the fetcher's ServiceAccount, in
  the release namespace, `create` on Leases, and `get` and `update` on the
  Lease `fetcher` only ([LEASE-1]).
- **Garage** finds its peers through Kubernetes with no definition and no
  ClusterRole of its own, holding only a Role in its namespace ([GARAGE-1]).
- **The definitions.** Each belongs to one of the four releases, and no
  component creates one ([CRD-6]).
- **Grafana is read-only.** It admits anonymous viewers with the `Viewer`
  role only, with its login form, basic authentication, and sign-up turned
  off, so no one signs in to change anything. Its data sources come only
  from its own value, and its dashboards only from the charts, and a
  provisioned dashboard can't be saved from its interface ([MON-2]). Its
  PostgreSQL data source isn't editable, and verifies the database server's
  certificate ([MON-5]). The dashboard is never a control ([0007]).
- **Alertmanager sends nothing outside the cluster:** its only receiver is
  `null` ([MON-3]).

## What each service may read and write

| Who | The cluster store's bucket, `snapshots` | The pipeline's database, `edgar` | The SEC |
| --- | --- | --- | --- |
| The fetcher | Reads and writes, with its own key. It stores an object only if none has its name, and never deletes or replaces one ([GARAGE-4], [OBJ-4], [LOG-4]) | Inserts and selects records, as its own role, and changes none ([LOG-3], [PG-8]) | Sends every request from the cluster, while it holds the Lease and requests are allowed ([FETCH-16], [FETCH-18], [ACCESS-1]) |
| The discoverer | Reads only, with its own key ([GARAGE-4]) | Inserts and selects the notices' tables, and selects records and the notices' views, as its own role, `discoverer`, and changes none ([PG-7], [PG-12], [TABLE-3], [LOG-3]) | Nothing ([FETCH-18]) |
| Each other service of the pipeline | If it uses the bucket, reads only, with a key of its own ([GARAGE-4], [LOG-4]) | If it uses the database, selects records and the discoverer's tables and views, as a role of its own, and changes none ([PG-7], [LOG-3], [TABLE-3], [PG-9], [PG-12], [PG-14]) | Nothing ([FETCH-18]) |
| Grafana | — | Uses the schema `dashboards` and selects its views, as its own role, `grafana`, and can neither read nor change a table or view of the record ([PG-11], [PG-13], [VIEW-3]) | Nothing ([FETCH-18]) |

What each service publishes and reads in NATS is on
[delivery and consistency](delivery-and-consistency.md#the-streams).

Each service has credentials of its own, and only the fetcher's may add
records and objects. Only the discoverer's may add to the notices' tables,
and Grafana's role reads only the views made for it
([deployment guarantees][the deployment's guarantees]).
No service connects as the database's owner, who could change a record, and
the database allows no superuser access ([PG-6],
[the store's database][stores-database]). Each service's role logs in
without superuser, `createdb`, `createrole`, `replication`, or `bypassrls`
([PG-7]), and no Garage key has the `owner` permission on any bucket, which
would let it change the bucket's configuration ([GARAGE-4]). So the database
refuses any change to a record. Garage can't refuse a change to an object,
so the pipeline makes none, and a check of the objects finds any other
([store guarantees][store-guarantees], [0002]).

The scheduler doesn't read EDGAR's content ([0006]): it never fetches and
never writes the record, and no controller is in the data path ([0007]). The
database role and Garage key of each service after the discoverer come with
that service, in later versions of the deployment specification
([later versions][deployment-later]).

Two boundaries inside the cluster have no statement yet. NATS has no
credential among those that [SECRET-1] lists, and no statement limits which
service publishes or reads which subjects. Nor does any statement give a
NetworkPolicy, which would limit which pods reach the SEC, NATS, Garage, or
the database.

## What runs

What the charts install is pinned, so an upgrade is a reviewed change, not
something resolved at install time
([deployment guarantees][the deployment's guarantees]). Every third-party
image is named by its digest as well as its tag ([PIN-4], [PIN-5]), nothing
comes from Bitnami ([CHART-3]), and every chart is committed at an exact
version ([PIN-1], [PIN-2]). The pipeline's own images are built by one
command from a commit with no uncommitted changes, tagged with that commit,
and loaded into kind's nodes, never pushed to or pulled from a registry
([IMG-1], [IMG-2]).

> **Analysis.** No task, decision record, or specification covers this.
>
> The two boundaries that have no statement could be drawn in a later
> version of the deployment specification. NATS's own accounts or keys, kept
> as Secrets rendered from required values like the other credentials, could
> limit each service to the subjects it publishes and reads, so that only
> the scheduler and the discoverer could queue a job. And NetworkPolicies
> could let only the fetcher's pods reach the SEC, so that what [FETCH-18]
> has as a rule of the services would also hold of the network.

[0002]: ../decisions/0002-store-bodies-in-garage.md
[0005]: ../decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0006]: ../decisions/0006-write-the-services-in-go-and-python.md
[0007]: ../decisions/0007-control-the-pipeline-through-custom-resources.md
[0010]: ../decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[API-3]: ../../spec/controls.md#4-the-resources
[CLIENT-2]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/fetching.md#4-requests
[CLIENT-3]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/fetching.md#4-requests
[store-guarantees]: ../../spec/snapshots.md#1-guarantees
[scope-cluster]: ../../spec/snapshots.md#the-scope-and-the-cluster
[OBJ-4]: ../../spec/snapshots.md#5-objects
[LOG-3]: ../../spec/snapshots.md#8-history
[LOG-4]: ../../spec/snapshots.md#8-history
[FETCH-2]: ../../spec/snapshots.md#10-fetching
[FETCH-15]: ../../spec/snapshots.md#10-fetching
[ID-1]: ../../spec/snapshots.md#10-fetching
[FETCH-16]: ../../spec/snapshots.md#the-fetcher
[FETCH-17]: ../../spec/snapshots.md#the-fetcher
[ID-2]: ../../spec/snapshots.md#the-fetcher
[FETCH-18]: ../../spec/snapshots.md#the-fetcher
[the deployment's guarantees]: ../../spec/deployment.md#1-guarantees
[deployment-terms]: ../../spec/deployment.md#2-terms
[CHART-3]: ../../spec/deployment.md#5-dependencies
[CRD-6]: ../../spec/deployment.md#definitions
[GARAGE-1]: ../../spec/deployment.md#garage
[GARAGE-2]: ../../spec/deployment.md#garage
[GARAGE-4]: ../../spec/deployment.md#garage
[MON-2]: ../../spec/deployment.md#monitoring
[MON-3]: ../../spec/deployment.md#monitoring
[MON-5]: ../../spec/deployment.md#monitoring
[credentials]: ../../spec/deployment.md#credentials
[SECRET-1]: ../../spec/deployment.md#credentials
[SECRET-2]: ../../spec/deployment.md#credentials
[SECRET-3]: ../../spec/deployment.md#credentials
[SECRET-4]: ../../spec/deployment.md#credentials
[LEASE-1]: ../../spec/deployment.md#the-fetchers-lease
[PIN-1]: ../../spec/deployment.md#6-pins
[PIN-2]: ../../spec/deployment.md#6-pins
[PIN-4]: ../../spec/deployment.md#6-pins
[PIN-5]: ../../spec/deployment.md#6-pins
[IMG-1]: ../../spec/deployment.md#the-pipelines-own-images
[IMG-2]: ../../spec/deployment.md#the-pipelines-own-images
[PG-4]: ../../spec/deployment.md#instances-and-backups
[stores-database]: ../../spec/deployment.md#the-stores-database
[PG-6]: ../../spec/deployment.md#the-stores-database
[PG-7]: ../../spec/deployment.md#the-stores-database
[PG-8]: ../../spec/deployment.md#the-stores-database
[PG-9]: ../../spec/deployment.md#the-stores-database
[PG-11]: ../../spec/deployment.md#the-notices-and-the-dashboards
[PG-12]: ../../spec/deployment.md#the-notices-and-the-dashboards
[PG-13]: ../../spec/deployment.md#the-notices-and-the-dashboards
[PG-14]: ../../spec/deployment.md#the-notices-and-the-dashboards
[deployment-later]: ../../spec/deployment.md#9-later-versions
[the controls' guarantees]: ../../spec/controls.md#1-guarantees
[controls-values]: ../../spec/controls.md#5-the-charts-values
[VAL-1]: ../../spec/controls.md#5-the-charts-values
[VAL-2]: ../../spec/controls.md#5-the-charts-values
[VAL-3]: ../../spec/controls.md#5-the-charts-values
[VAL-4]: ../../spec/controls.md#5-the-charts-values
[controls-secaccess]: ../../spec/controls.md#8-secaccess
[ACCESS-1]: ../../spec/controls.md#8-secaccess
[who may change what]: ../../spec/controls.md#9-who-may-change-what
[RBAC-1]: ../../spec/controls.md#9-who-may-change-what
[RBAC-2]: ../../spec/controls.md#9-who-may-change-what
[RBAC-3]: ../../spec/controls.md#9-who-may-change-what
[RBAC-4]: ../../spec/controls.md#9-who-may-change-what
[RBAC-5]: ../../spec/controls.md#9-who-may-change-what
[RBAC-6]: ../../spec/controls.md#9-who-may-change-what
[TABLE-3]: ../../spec/notices.md#4-the-record-of-notices
[the notices' Backfills]: ../../spec/notices.md#8-backfills
[METRIC-2]: ../../spec/telemetry.md#4-metrics
[VIEW-3]: ../../spec/telemetry.md#8-the-views-grafana-reads
[TRACE-5]: ../../spec/telemetry.md#9-traces
[TRACE-6]: ../../spec/telemetry.md#9-traces
