=================
How to contribute
=================

By coding
=========

Whenever you start working on a bug or an issue, please mention it in the
corresponding issue on this repo. If there is not an already opened issue for
this bug, please open a MR as soon as possible (with the ``WIP:`` prefix
mentioning it is a work in progress) to let others know you are working on
this module and fixing things.

This way, everyone is aware of the changes you are making and this avoid doing
a lot of duplicate work.

Write a patch
-------------

Help yourself with the `documentation <http://woob.dev/>`_.

Find an opened issue on `this website <https://gitlab.com/woob/woob/issues>`_, or write your own bugfix or feature.
Then, once it is necessary, commit with::

    $ git commit -a

Do not forget to write a helpful commit message. If you are fixing a bug in a
specific module, the first line of your commit message should read
``[module_name] Description of the fix``.


Check your patch
----------------

As the project codebase is pretty large, having a consistent developer experience (coding style, etc) is primordial.

Woob uses `pre-commit <https://pre-commit.com/>`_ to apply most of the project's policies to your commits.
These checks are also executed on the `Gitlab CI <https://gitlab.com/woob/woob/pipelines>`_ but
having pre-commit installed locally allows getting feedback as soon as possible.

To use pre-commit, install it either from your package manager or from pip and
enable it to manage git hooks in your checkout::

    $ pre-commit install
    pre-commit installed at .git/hooks/commit-msg
    pre-commit installed at .git/hooks/pre-commit

After this, you may work with git as usual and it will trigger registered hooks as appropriate.

The project also provides `tox <https://tox.wiki/>`_ configuration to easily run tests and
other development tools in a reproducible manner.
Like pre-commit, tox is used in the Gitlab CI so creating merge request should be a walk in the park.

To use tox, install it either from your package manager or from pip and run it. No extra setup is required.

.. code-block::

    $ tox run
    [...]
      py311: SKIP (0.05 seconds)
      py312: OK (42.41=setup[23.54]+cmd[18.87] seconds)
      py313: OK (27.14=setup[13.95]+cmd[13.19] seconds)
      bandit: OK (51.00=setup[13.18]+cmd[4.88,32.38,0.56] seconds)
      build: OK (20.17=setup[14.62]+cmd[5.04,0.52] seconds)
      congratulations :) (141.48 seconds)

You can also run these scripts to be sure your patch doesn't break anything::

    $ tools/pyflakes.sh
    $ tools/woob_lint.sh
    $ tools/run_tests.sh yourmodulename  # or without yourmodulename to test everything

Perhaps you should also write or fix tests.
These tests are automatically run by Gitlab CI at each commit and merge requests.

Create a merge request or send a patch
--------------------------------------

The easiest way to send your patch is to create a fork on `the woob gitlab <https://gitlab.com/woob/woob/>`_ and create a merge
request from there. This way, the code review process is easier and continuous integration is run automatically (see
previous section).

Notes on merging a merge request
--------------------------------

Few people (members of the `Woob group on this
repo <https://gitlab.com/groups/woob/-/group_members>`_) have the right to
merge a MR.

Anyone is welcome to review and comment pending merge requests. A merge
request should in principle have at least two reviewers before getting merged.

Woob repo should keep an history as linear as possible. Then, merging a merge
request should be done locally, with prior rebasing upon the ``master`` branch
and take care of using the ``-ff-only`` merge option. Merge requests should
**NOT** be merged through the Gitlab UI, which would result in an extra "merge"
commit.

Getting your contribution accepted
----------------------------------

All contributions are welcome and will only be judged on a technical and legal merit.
Contributing does not require endorsing views of any other contributor,
or supporting the project in any way.

Rejected contributions are not personal; further contributions will be considered.

It is discouraged to inquire about any contributor opinions or
identity characteristics as they should not have any influence on the quality
of the contribution. It is also possible to contribute anonymously.

If provided, icons are preferred to be parodic or humorous in nature for
legal reasons, however there are no restrictions on the quality or style of humor.

.. _contribute-tests:

Automated tests
===============

Summary
-------

Woob is a wide project which has several backends and applications, and changes can impact a lot of subsystems. To be sure that everything works fine after an important change, it's necessary to have automated tests on each subsystems.

How it works
------------

You need `pytest <https://docs.pytest.org/>`_ installed.

To run the automated tests, use this script::

    $ tools/run_tests.sh

It looks for every files named ``test.py``, and find classes derivated from :py:class:`TestCase` of :class:`~woob.tools.test.BackendTest`.

Then, it run every method which name starts with ``test_``.

.. note::
    Some environment variables are available, to use specific backend file or send the test results. Refer to `the
    comments in the script <https://gitlab.com/woob/woob/blob/master/tools/run_tests.sh#L4-8>`_ for more infos on this.

If a module name is passed as argument, only this module will be tested. For example, to only run ``lutim`` tests::

    $ tools/run_tests.sh lutim

To test with a Python 3 interpreter, set the ``-3`` flag (before all other arguments)::

    $ tools/run_tests.sh -3

Alternatively, you can use tox::

    $ tox run

Write a test case
-----------------

Normal test
~~~~~~~~~~~

Use the class :class:`~woob.tools.test.TestCase` to derivate it into your new test case. Then, write methods which name starts with ``test_``.

A test fails when an assertion error is raised. Also, when an other kind of exception is raised, this is an error.

You can use ``assert`` to check something, or the base methods ``assertTrue``, ``assertIf``, ``failUnless``, etc. Read the `unittest documentation <http://docs.python.org/library/unittest.html>`_ to know more.

Backend test
~~~~~~~~~~~~

Create a class derivated from :class:`~woob.tools.test.BackendTest`, and set the ``BACKEND`` class attribute to the name of the backend to test.

Then, in your test methods, the ``backend`` attribute will contain the loaded backend. When the class is instancied, it loads every configured backends of the right type, and randomly choose one.
If no one is found, the tests are skipped.

Example::

    from woob.tools.test import BackendTest

    class YoutubeTest(BackendTest):
        MODULE = 'youtube'

        def test_youtube(self):
            l = [v for v in self.backend.iter_search_results('lol')]
            self.assertTrue(len(l) > 0)
            v = l[0]
            self.backend.fillobj(v, ('url',))
            self.assertTrue(v.url and v.url.startswith('https://'), f'URL for video {v.id} not found: {v.url}')

Note: :class:`~woob.tools.test.BackendTest` inherits :py:class:`TestCase`, so the checks work exactly the same, and you can use the same base methods.
