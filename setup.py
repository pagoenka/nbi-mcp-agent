#!/usr/bin/env python
from setuptools import setup, find_packages
from .nbi_mcp_agent._version import __version__


setup(
    name='nbi_mcp_agent',
    version=__version__,
    packages=find_packages(),
    include_package_data=True
)
