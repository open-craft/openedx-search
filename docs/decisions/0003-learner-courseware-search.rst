0003 Learner Courseware Search
##############################

Status
******

**Proposed**

Context
*******

`0001-purpose-of-this-repo.rst`_ makes learner courseware search one of the
use cases of ``openedx-search``, and Decision 7 says the browser queries the
engine directly with a scoped, expiring credential.
`0002-schema-declarations-and-adapter-contract.rst`_ settles the schema and the
adapter contract. This ADR settles what those leave open for learner search:
whether it shares an index with Studio, which documents and fields it needs,
how the credential limits results to the learner's own content, and how a query
is shaped.

The current learner surface
===========================

Learner courseware search is the search bar in the courseware. The Learning MFE
shows it when the ``courseware.mfe_courseware_search`` waffle flag is enabled,
and the platform also gates it on ``ENABLE_COURSEWARE_INDEX`` and
``ENABLE_COURSEWARE_SEARCH``.

It is currently served by ``edx-search``:

* The CMS indexes the published course with ``CoursewareSearchIndexer`` in
  ``cms/djangoapps/contentstore/courseware_index.py``. The index is
  ``courseware_content``. The indexer walks the published branch of the
  modulestore, calls each block's ``index_dictionary()``, and adds ``course``,
  ``org``, ``id``, ``start_date``, ``content_groups``, ``course_name`` and a
  three-level ``location`` path.
* The LMS serves ``POST /search/<course_id>`` from ``search.views.do_search``,
  which calls ``search.api.perform_search``. The filter generator
  ``LmsSearchFilterGenerator`` limits the query to the user's enrolled courses,
  the site's orgs and the course when one is given.
* Each result is post-processed by ``LmsSearchResultProcessor``. Its
  ``should_remove`` method calls ``get_course_blocks(user, course)`` and drops
  any result the user cannot see. That is the current authorization step. For
  example, a course runs two cohorts, A and B. A problem restricted to cohort B
  matches the query "checkpoint". The learner is in cohort A but is enrolled in
  the course, so the engine returns the problem; the index knows
  ``content_groups``, but the current query does not filter on it.
  ``should_remove`` calls ``get_course_blocks``, the
  ``UserPartitionTransformer`` excludes the problem, and the result is dropped.
  A randomized library problem the learner was not assigned is dropped the same
  way.
* ``get_course_blocks`` applies the course-block transformers:
  ``StartDateTransformer``, ``UserPartitionTransformer``,
  ``VisibilityTransformer``, ``ContentLibraryTransformer`` and
  ``ContentLibraryOrderTransformer``, ``ContentTypeGateTransformer``, the
  per-user ``DateOverrideTransformer``, and, when the individual-student
  override provider is configured, ``OverrideDataTransformer``. The visible
  set is therefore a function of the user, the cohort or group, the release
  date and several per-user overrides.

The search is per course, relevance-ordered, and paged. The current endpoint
accepts ``search_string``, ``page_size`` (default 20, maximum 100) and
``page_index``.

Two questions this ADR answers
==============================

ADR 0001 left two questions open for this surface:

* Should Studio courseware search and learner courseware search share one
  index?
* How do we account for complex learner XBlock visibility permissions?

Decision
********

1. Learner content gets its own index
=====================================

Learner courseware search registers its own use case and index,
``learner_courseware``. It does not share an index with the Studio use cases:

.. list-table::
   :header-rows: 1

   * - Index
     - Use case
     - Contents
     - Access
   * - ``learner_courseware``
     - ``learner_courseware``
     - Published course blocks only
     - Course credential: enrollment, groups, release date
   * - ``studio_content``
     - ``studio_course``
     - Course authoring blocks, drafts included
     - Authoring tenant
   * - ``studio_library_content``
     - ``studio_library``
     - Library authoring blocks
     - Authoring tenant

The three registrations may share the parts of the document builder and schema
that describe the same fields, but they are separate registrations with
separate versions and rebuilds. The Studio registrations keep the existing
``content/search`` index names, ``studio_content`` and
``studio_library_content``, so adopting the library does not rename them.

The reasons are the ones that split the Studio course and library indexes in
``content/search``'s ADR 0002, "Separate Meilisearch Indexes for Course and
Library Content":

* **Visibility.** The authoring indexes contain drafts and the ``published``
  mirror. The learner index must contain published content only. A shared index
  turns every learner query into a query that must also be correct about
  drafts, and one missing filter exposes unpublished content.
* **Access.** Authoring access is a tenant boundary (org or access ID).
  Learner access is enrollment, cohort or group membership and release dates.
  The two filters have different shapes and are minted by different services.
* **Content set.** The authoring indexes contain course and library content.
  The learner index contains one course's published blocks and no libraries.
* **Size and query cost.** Learner search is a per-course query over a small
  slice of the corpus. Keeping it separate lets the index stay smaller than the
  authoring index, and keeps a course search from scanning authoring documents.
* **Rebuild cadence.** A learner schema change must not force a Studio rebuild,
  and the authoring write volume must not affect the learner index.

This answers ADR 0001's open question: **two separate indexes**, not one.

2. The schema describes published learner documents
===================================================

One document is one published block that returns an ``index_dictionary``, the
same set the current indexer writes. The schema is version 1:

.. code-block:: python

   IndexSchema(
       name="learner_courseware",
       version=1,
       fields=(
           Field("id", FieldType.STRING, required=True),
           Field("usage_key", FieldType.STRING, required=True),
           Field("course", FieldType.STRING, required=True,
                 capabilities=Capabilities(filterable=True)),
           Field("display_name", FieldType.STRING, required=True,
                 capabilities=Capabilities(searchable=Searchable(order=1))),
           Field("block_type", FieldType.STRING, required=True),
           Field("content", FieldType.OBJECT, additional_properties=True,
                 capabilities=Capabilities(searchable=Searchable(order=2))),
           Field("content.problem_types", FieldType.STRING, array=True),
           Field("breadcrumbs", FieldType.OBJECT, array=True, required=True),
           Field("breadcrumbs.display_name", FieldType.STRING, array=True),
           Field("breadcrumbs.usage_key", FieldType.STRING, array=True),
           Field("start_date", FieldType.DATETIME, required=True,
                 capabilities=Capabilities(filterable=True)),
           Field("content_groups", FieldType.STRING, array=True, required=True,
                 capabilities=Capabilities(filterable=True)),
       ),
   )

.. list-table::
   :header-rows: 1

   * - Field
     - Notes
   * - ``id``
     - Engine-safe id derived with ``document_id_from_key(usage_key)``. The
       usage key contains ``:``, ``+`` and ``@``, which the engines reject.
   * - ``usage_key``
     - The block's usage key, used to navigate to the block. Not searchable
       and not filterable.
   * - ``course``
     - The course key. The credential always restricts it, so the field is
       filterable but not facetable.
   * - ``display_name``
     - The block name. First searchable attribute.
   * - ``block_type``
     - The block category from the usage key (``html``, ``video``,
       ``problem``, ``vertical``, ...). Used to render the result, not to
       search or filter it.
   * - ``content``
     - The block-type content returned by ``index_dictionary()``. A free-form
       object whose values are strings, so a new block type does not need a
       schema change for its text.
   * - ``content.problem_types``
     - The known array-valued content field problem blocks emit. Any other
       non-string content field the supported block types emit must be
       declared the same way or flattened by the builder.
   * - ``breadcrumbs``
     - The ancestor path, with names for display and usage keys for linking.
       The old index kept names only; the keys cost little and let the
       frontend build links without a second lookup.
   * - ``start_date``
     - The release date used by the credential filter. A block without its
       own start inherits the course start; a course without a start uses the
       epoch, so "released" is the default.
   * - ``content_groups``
     - The groups that may see the block. ``["public"]`` means every enrolled
       learner; otherwise it contains the cohort and group-partition IDs from
       ``GroupConfiguration``.

Not modelled: ``org`` and site filters (the course key already scopes the
query), ``access_id`` (learner access is enrollment, not a restricted tenant),
``catalog_visibility`` and ``invitation_only`` (course-level, checked when that
course's credential is minted), ``published`` and the authoring mirror (the
index is published-only), and library fields (libraries are not courseware).

3. The browser queries the engine with a course credential
==========================================================

The LMS mints the credential after it has checked that the user is enrolled and
may access the course. The credential is an ordinary
``SearchCredential``: public engine URL, ``learner_courseware`` as the physical
index, and a tenant token that embeds this filter:

.. code-block:: text

   course IN ["course-v1:edX+DemoX+2026"]
   AND content_groups IN ["public", "cohort-a", "group-3"]
   AND start_date <= 1798761600

The timestamp is the mint time, not the token's expiry. It hides blocks whose
release date is still in the future, so the credential never shows content
that was unreleased when it was minted. The cost is that a block released
during the token's lifetime stays out of search until the frontend mints the
next token, at most one lifetime later. Using the expiry instead would show
published but unreleased blocks before their release, which is the wrong
failure mode.

A token is minted per user and course, is short-lived (one hour by default) and
is refreshed by the frontend when it expires. A user removed from the course
keeps a token only until it expires, so the lifetime is also the revocation
window.

The MFE is scoped to one course, so one credential per course is enough. A
credential covering several courses would carry enrollment and group facts the
page does not need, and would widen what a leaked token can reach.

Staff credentials omit the ``content_groups`` term, so staff can search every
published block in the course. Drafts remain Studio's business and are not in
this index.

The MFE and the shared frontend library (``openedx/frontend-base#323``, TBD) query
the engine directly with this credential. The LMS does not proxy the query, and
the ``get_course_blocks`` post-filter is not in the query path. The engine
applies the embedded filter to every search.

`0002-schema-declarations-and-adapter-contract.rst`_ describes
``FilterExpression`` as exact matches, with ranges extending ``FilterTerm``
rather than the expression shape. The ``start_date`` term is that range: the
credential API must accept a ``<=`` range before learner search can ship.

4. Visibility the filter cannot express is not indexed
======================================================

The filter expresses course, group and release-date visibility. Anything else
that can change the visible set is handled at indexing time, and the rule is
**fail closed**: a block whose visibility cannot be expressed is not indexed
for learners. It remains navigable in the course, but it is not a search
result.

.. list-table::
   :header-rows: 1

   * - Access transformer
     - Learner search handling
   * - ``StartDateTransformer``
     - ``start_date <= now`` in the credential. A per-user date override
       (``DateOverrideTransformer``) is not represented; the block becomes
       searchable at its standard date, which is a false negative, not a
       leak.
   * - ``UserPartitionTransformer``
     - ``content_groups``; the credential carries the user's cohort and group
       IDs. This is the rule the filter exists for.
   * - ``VisibilityTransformer``
     - Blocks hidden from learners (``visible_to_staff_only`` and similar)
       are not indexed.
   * - ``ContentLibraryTransformer`` and ``ContentLibraryOrderTransformer``
     - Randomized library content selects different blocks for different
       students. The selection is per user and cannot be a static field, so
       ``library_content`` subtrees are not indexed for learners in version 1.
   * - ``ContentTypeGateTransformer``
     - The search feature flag is limited to verified learners and staff, who
       pass the gate. If the flag is ever opened to audit learners, the gate
       becomes a credential field or gated blocks stop being indexed; it is
       not silently ignored.
   * - ``OverrideDataTransformer``
     - When the individual-student override provider is configured, per-user
       field overrides are not represented. The block is searchable under its
       standard values; again a false negative, not a leak.
   * - ``MilestonesAndSpecialExamsTransformer``
     - The transformer exists in the blocks API, but it is not in the default
       ``get_course_blocks`` list and is not a search gate today. A new
       per-user rule follows the fail-closed rule until the access-ID model can
       express it.

The LMS remains the authority for enrollment and course access at mint time.
The engine is the authority for the expressible per-block rules in the filter.
A bug on either side can expose content, so the indexing and minting code need
tests that assert isolation: a token carrying group A must not return a block
that only group B may see, and a missing ``content_groups`` value must fail
closed rather than default to public.

This answers ADR 0001's second question. The complex rules are not pushed into
the engine wholesale; they are reduced to the three fields the filter can
enforce, and the remainder is excluded rather than approximated.

5. Queries are relevance-only and paged
=======================================

The learner search sends text, a page and a page size. It sends no sort and no
user-selected facets in version 1, because the MFE has neither. Relevance is
the engine's ranking, with the ``sort`` rule from ADR 0002 inert because no
sort parameter is present. The page size is 20 by default and 100 at most, the
same contract as today.

The credential's filters apply to every query, so the frontend cannot widen
them. The result document carries ``usage_key`` for navigation,
``display_name``, ``breadcrumbs``, ``block_type`` and ``content``. Highlighting
and excerpts are rendered by the frontend library from the engine's native
highlight fields; the LMS no longer computes an HTML excerpt with ``<b>`` tags,
and the frontend must sanitize rendered content.

The total is the engine's total. On Typesense the reported total can be capped
by the collection's facet and pagination limits, so the frontend must treat it
as an estimate when the engine says it is not exact, per ADR 0002's
``SearchResult``.

The library's server-side ``search`` API is not the learner frontend path. It
stays for maintenance and conformance, as ADR 0002 describes.

6. Indexing stays with the published modulestore
================================================

The consumer's indexing pipeline writes the learner index. In the platform that
is the ``content/search`` app in the CMS, which already owns the course and
library documents:

* On publish and on a full reindex, it walks the published branch of the
  modulestore and builds the learner documents from the same
  ``index_dictionary`` output and breadcrumb walk as the Studio documents.
* It computes ``content_groups`` from ``GroupConfiguration`` and the split-test
  and group-partition usage, writes ``["public"]`` when a block is unrestricted,
  and writes the release date or its default.
* It submits bounded batches with ``openedx_search.api.upsert``, deletes
  removed blocks with ``delete``, and confirms writes with ``check_write`` from
  the indexing worker.
* The LMS never writes the index. It only mints credentials.

The use case is a shared platform declaration, not an object owned by a
process. The schema and the registration live in a module under the platform's
``openedx`` package, not in the CMS-only ``content/search`` app, so both
services (LMS and CMS) can import it. Each process registers it from its own
``AppConfig.ready()``: the CMS for the write path, the LMS for the credential
path. The library's registry is per process, so the CMS does not export it to
the LMS; the declaration is the shared contract. The CMS owns the write
pipeline, the LMS owns the mint, and the schema belongs to neither service
alone.

The learner index is versioned independently of the Studio indexes. A
same-version reindex dual-writes while it runs; a schema version bump is a
migration with writers stopped, per ADR 0002's rebuild policy. Rebuilding the
learner index does not touch the Studio indexes, and the reverse.

The current ``CoursewareSearchIndexer`` and the ``LmsSearchFilterGenerator``,
``LmsSearchInitializer`` and ``LmsSearchResultProcessor`` overrides are removed
with the old path in ``openedx/frontend-app-learning#2184``, after the new path
is validated. The ``edx-search`` dependency is retired in
``openedx/edx-search#296``.

7. Rollout
==========

The existing consumer flags remain the rollout controls:
``ENABLE_COURSEWARE_INDEX``, ``ENABLE_COURSEWARE_SEARCH`` and
``courseware.mfe_courseware_search``. The library has no search enable flag of
its own.

The path is introduced side by side. The index is built and reconciled while
the current ``edx-search`` path keeps serving the MFE. Before the switch, a
temporary comparison job runs a sample of queries for a sample of courses and
users against the old ``edx-search`` API and the new index, and reports the
result keys that differ. The new side can use the library's maintenance
``search`` API with the same course and group filters. The job is a validation
step, not a permanent query path and not a change to ``edx-search``; the
expected differences are the fail-closed omissions, and they are reviewed
before the MFE switches to the credential. The old index and the LMS overrides
are removed after the switch, and the old ``courseware_content`` index is
dropped only after a rollback window.

Consequences
************

* **Two indexes, two rebuilds.** Learner and Studio content are rebuilt
  independently. That is more operational work than one index, and it is the
  price of not exposing drafts, not scanning authoring content and not tying
  the two schemas' versions together.
* **Fail-closed content is not searchable.** Randomized library content,
  per-user overrides and hidden blocks are navigable but absent from search.
  This is a functional regression from today's post-filter, where some of them
  appear for the students who can see them. It is accepted for version 1 and
  recorded as follow-up work.
* **The credential API needs a range term.** ``start_date <= now`` is not an
  exact match. The ``CredentialScope``/``FilterTerm`` extension is a
  prerequisite for this surface, not an optional refinement.
* **Authorization moves into two places.** The LMS checks enrollment and course
  access at mint time; the index fields enforce the per-block rules. Tests must
  cover the negative cases, because an indexing or minting bug is a data
  exposure, not a bad result.
* **No LMS query path.** The Learning MFE calls the engine, so the LMS no
  longer pays a request thread per keystroke and no longer needs the block
  structure to serve a search. The frontend carries the credential lifecycle
  and the engine's errors.
* **Published-only.** Staff search in the learner index sees published content
  only. Draft search remains the Studio surface.
* **Independent versions.** A learner schema change does not rebuild Studio,
  and a Studio change does not rebuild learners.
* **The old code goes away.** The ``content/search`` courseware indexer and the
  LMS search overrides are removed with the ``edx-search`` dependency, not
  carried as a second implementation.

Open Questions
**************

* **Content-type gate.** Today the MFE search flag is limited to verified
  learners and staff, so the gate and the search audience coincide. If the flag
  is opened to audit learners, the gate must become a credential field or the
  gated blocks must stop being indexed. Which one is a product decision.
* **Randomized library content.** Version 1 omits it. A future credential could
  carry the selected block IDs for the user (or some kind of "access_id", see
  below), at the cost of token size and a per-user computation. It is not in this
  ADR.
* **Access IDs.** ``openedx/openedx-search#11`` may define a general access-ID
  model. Learner search uses course and groups because enrollment is the access
  source; if the model also fits learner access, the ``content_groups`` field
  can be replaced or augmented without changing the rest of this decision.
* **Credential lifetime.** One hour is a starting point. The right value
  balances revocation latency against re-minting traffic, and belongs with the
  frontend library's credential cache.

Rejected Alternatives
*********************

One shared Studio and learner index
===================================

Rejected because the two audiences need different visibility and access
filters, the learner index must exclude drafts and libraries, and a shared
index makes every learner query pay for the authoring corpus. The scaling
problem in ``openedx/openedx-platform#38993`` is the same failure mode at a
larger scale. Sharing the document builder and schema fragments gets the code
reuse without sharing the index.

Keep the LMS in the query path
==============================

The current design post-filters every result with ``get_course_blocks``. It
preserves every access rule, including per-user overrides, and it is the
simplest way to remain exactly compatible. It is rejected because ADR 0001
Decision 7 keeps Django out of the query path, and because the post-filter is
the performance cost the library exists to remove. The fail-closed omissions
are the accepted trade. If full parity becomes a product requirement, a
server-side search path for those users or courses is the fallback, not a
reason to put every search back through the LMS.

References
**********

.. _0001-purpose-of-this-repo.rst: ./0001-purpose-of-this-repo.rst
.. _0002-schema-declarations-and-adapter-contract.rst: ./0002-schema-declarations-and-adapter-contract.rst

* ADR 0001, "Purpose of This Repo" -
  ``docs/decisions/0001-purpose-of-this-repo.rst``
* ADR 0002, "Use-Case Schema Declarations and Adapter Contract" -
  ``docs/decisions/0002-schema-declarations-and-adapter-contract.rst``
* ``content/search`` ADR 0002, "Separate Meilisearch Indexes for Course and
  Library Content" -
  https://github.com/openedx/openedx-platform/blob/master/openedx/core/djangoapps/content/search/docs/decisions/0002-separate-course-and-library-indexes.rst
* ``cms/djangoapps/contentstore/courseware_index.py``, the current courseware
  indexer -
  https://github.com/openedx/openedx-platform/blob/master/cms/djangoapps/contentstore/courseware_index.py
* ``lms/lib/courseware_search/``, the current LMS filter, initializer and
  result overrides -
  https://github.com/openedx/openedx-platform/tree/master/lms/lib/courseware_search
* ``lms/djangoapps/course_blocks/api.py``, the course-block access
  transformers -
  https://github.com/openedx/openedx-platform/blob/master/lms/djangoapps/course_blocks/api.py
* ``openedx/openedx-search#10``, ADR for learner courseware search -
  https://github.com/openedx/openedx-search/issues/10
* ``openedx/openedx-search#12``, the restricted tenant token API -
  https://github.com/openedx/openedx-search/issues/12
