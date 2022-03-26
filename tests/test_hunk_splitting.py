from __future__ import annotations

import io
from collections.abc import Sequence
from functools import partial
from textwrap import dedent

import pytest

from git_crecord.crpatch import filterpatch, parsepatch


def line_selector(selections: Sequence[bool], opts, headers, ui):
    for i, hunkline in enumerate(headers[0].hunks[0].changedlines):
        hunkline.applied = selections[i]


@pytest.mark.parametrize(
    ("selections", "expected"),
    [
        pytest.param(
            [True, False, True, False],
            '''
            @@ -1,3 +1,3 @@
             RUN apt-get update
            - && apt-get install -y supervisor python3.8
            + && apt-get install -y supervisor python3.9
                 git python3-pip ssl-cert
            ''',
            id="symmetric",
        ),
        pytest.param(
            [True, True, True, False],
            '''
            @@ -1,3 +1,2 @@
             RUN apt-get update
            - && apt-get install -y supervisor python3.8
            + && apt-get install -y supervisor python3.9
            -    git python3-pip ssl-cert
            ''',
            id="extra deletion",
        ),
        pytest.param(
            [True, False, True, True],
            '''
            @@ -1,3 +1,4 @@
             RUN apt-get update
            - && apt-get install -y supervisor python3.8
            + && apt-get install -y supervisor python3.9
                 git python3-pip ssl-cert
            +    git python3-pip ssl-cert time
            ''',
            id="extra addition",
        ),
    ],
)
def test_hunk_splitting(selections: Sequence[bool], expected: str):
    diff = dedent(
        '''
        diff --git a/Dockerfile b/Dockerfile
        index 00083f466d4f..400252a22712 100644
        --- a/Dockerfile
        +++ b/Dockerfile
        @@ -1,3 +1,3 @@
         RUN apt-get update
        - && apt-get install -y supervisor python3.8
        -    git python3-pip ssl-cert
        + && apt-get install -y supervisor python3.9
        +    git python3-pip ssl-cert time
        ''',
    ).lstrip('\n').encode()
    patch = parsepatch(io.BytesIO(diff))
    applied = filterpatch(None, patch, partial(line_selector, selections), None)

    assert str(applied.hunks[0]) == dedent(expected).lstrip('\n')
