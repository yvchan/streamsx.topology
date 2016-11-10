from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division
from __future__ import absolute_import
try:
    from future import standard_library
    standard_library.install_aliases()
except (ImportError, NameError):
    # nothing to do here
    pass
# Licensed Materials - Property of IBM
# Copyright IBM Corp. 2015

import logging


import tempfile
import os
import os.path
import json
import subprocess
import threading
import sys

from platform import python_version

logger = logging.getLogger('streamsx.topology.py_submit')


#
# Submission of a python graph using the Java Application API
# The JAA is reused to have a single set of code_createJSONFile that creates
# SPL, the toolkit, the bundle and submits it to the relevant
# environment
#
def submit(ctxtype, app_topology, config=None, username=None, password=None, rest_api_url=None, log_level=logging.INFO):
    """
    Submits a topology with the specified context type.
    
    Args:
        ctxtype (string): context type.  Values include:
        * DISTRIBUTED - the topology is submitted to a Streams instance.
          The bundle is submitted using `streamtool` which must be setup to submit without requiring authentication input
        * STANDALONE - the topology is executed directly as an Streams standalone application.
          The standalone execution is spawned as a separate process
        * BUNDLE - execution of the topology produces an SPL application bundle
          (.sab file) that can be submitted to an IBM Streams instance as a distributed application.
        * JUPYTER - the topology is run in standalone mode, and context.submit returns a stdout streams of bytes which 
          can be read from to visualize the output of the application.
        app_topology: a Topology object or Topology.graph object
        
    Returns:
        An output stream of bytes if submitting with JUPYTER, otherwise returns None.
    """    
    logger.setLevel(log_level)
    context_submitter = _SubmitContextFactory(app_topology, config, username, password, rest_api_url)\
        .get_submit_context(ctxtype)
    try:
        context_submitter.submit()
    except:
        logger.exception("Error while submitting application.")


class _BaseSubmitter:
    """
    A submitter which handles submit operations common across all submitter types..
    """
    def __init__(self, ctxtype, config, app_topology):
        self.ctxtype = ctxtype
        self.config = config
        self.app_topology = app_topology

        # encode the relevant python version information into the config
        self._do_pyversion_initialization()

        # Create the json file containing the representation of the application
        try:
            self.fn = self._create_json_file(self._create_full_json())
        except Exception:
            logger.exception("Error generating SPL and creating JSON file.")
            raise

    def submit(self):
        tk_root = self._get_toolkit_root()

        cp = os.path.join(tk_root, "lib", "com.ibm.streamsx.topology.jar")

        streams_install = os.environ.get('STREAMS_INSTALL')
        # If there is no streams install, get java from JAVA_HOME and use the remote contexts.
        if streams_install is None:
            java_home = os.environ.get('JAVA_HOME')
            if java_home is None:
                raise "Please set the JAVA_HOME system variable"

            jvm = os.path.join(java_home, "bin", "java")
            submit_class = "com.ibm.streamsx.topology.context.remote.RemoteContextSubmit"
        # Otherwise, use the Java version from the streams install
        else:
            jvm = os.path.join(streams_install, "java", "jre", "bin", "java")
            submit_class = "com.ibm.streamsx.topology.context.StreamsContextSubmit"
            cp = cp + ':' + os.path.join(streams_install, "lib", "com.ibm.streams.operator.samples.jar")

        args = [jvm, '-classpath', cp, submit_class, self.ctxtype, self.fn]
        process = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        try:
            stderr_thread = threading.Thread(target=_print_process_stderr, args=([process, self.fn]))
            stderr_thread.daemon = True
            stderr_thread.start()

            stdout_thread = threading.Thread(target=_print_process_stdout, args=([process]))
            stdout_thread.daemon = True
            stdout_thread.start()
            process.wait()
            return None

        except:
            logger.exception("Error starting java subprocess for submission")
            raise

    def _do_pyversion_initialization(self):
        # path to python binary
        pythonbin = sys.executable
        pythonreal = os.path.realpath(pythonbin)
        pythondir = os.path.dirname(pythonbin)
        pythonrealfile = os.path.basename(pythonreal)
        pythonrealconfig = os.path.realpath(pythondir + "/" + pythonrealfile + "-config")
        pythonversion = python_version()

        # place the fullpaths to the python binary that is running and
        # the python-config that will used into the config
        self.config["pythonversion"] = {}
        self.config["pythonversion"]["version"] = pythonversion
        self.config["pythonversion"]["binaries"] = []
        bf = dict()
        bf["python"] = pythonreal
        bf["pythonconfig"] = pythonrealconfig
        self.config["pythonversion"]["binaries"].append(bf)

    def _create_full_json(self):
        fj = dict()
        fj["deploy"] = self.config
        fj["graph"] = self.app_topology.generateSPLGraph()
        return fj

    def _create_json_file(self, fj):
        if sys.hexversion < 0x03000000:
            tf = tempfile.NamedTemporaryFile(mode="w+t", suffix=".json", prefix="splpytmp", delete=False)
        else:
            tf = tempfile.NamedTemporaryFile(mode="w+t", suffix=".json", encoding="UTF-8", prefix="splpytmp",
                                             delete=False)
        tf.write(json.dumps(fj, sort_keys=True, indent=2, separators=(',', ': ')))
        tf.close()
        return tf.name

    # There are two modes for execution.
    #
    # Pypi (Python focused)
    #  Pypi (pip install) package includes the SPL toolkit as
    #      streamsx/.toolkit/com.ibm.streamsx.topology
    #      However the streamsx Python packages have been moved out
    #      of the toolkit's (opt/python/package) compared
    #      to the original toolkit layout. They are moved to the
    #      top level of the pypi package.
    #
    # SPL Toolkit (SPL focused):
    #   Streamsx Python packages are executed from opt/python/packages
    #
    # This function determines the root of the SPL toolkit based
    # upon the existance of the '.toolkit' directory.
    #
    @staticmethod
    def _get_toolkit_root():
        # Directory of this file (streamsx/topology)
        dir = os.path.dirname(os.path.abspath(__file__))

        # This is streamsx
        dir = os.path.dirname(dir)

        # See if .toolkit exists, if so executing from
        # a pip install
        tk_root = os.path.join(dir, '.toolkit', 'com.ibm.streamsx.topology')
        if os.path.isdir(tk_root):
            return tk_root

        # Else dir is tk/opt/python/packages/streamsx

        dir = os.path.dirname(dir)
        dir = os.path.dirname(dir)
        dir = os.path.dirname(dir)
        tk_root = os.path.dirname(dir)
        return tk_root


class _RemoteBuildSubmitter(_BaseSubmitter):
    """
    A submitter which retrieves the SWS REST API URL and then submits the application to be built and submitted
    on BlueMix within a Streaming Analytics service.
    """
    def __init__(self, ctxtype, config, app_topology):
        _BaseSubmitter.__init__(self, ctxtype, config, app_topology)

        # Get the username, password, and rest API URL
        services = config['topology.service.vcap']['streaming-analytics']
        creds = None
        for service in services:
            if service['name'] == config['topology.service.name']:
                creds = service['credentials']
                break
        if creds is None:
            raise ValueError(config['topology.service.name'] + " service was not found in the supplied VCAP")
        username = creds['userid']
        password = creds['password']

        # Obtain REST only when needed. Otherwise, submitting "Hello World" without requests fails.
        try:
            import requests
        except (ImportError, NameError):
            logger.exception('Unable to import the optional "Requests" module. This is needed when performing'
                             ' a remote build or retrieving view data.')
            raise

        # Obtain the streams SWS REST URL
        resources_url = creds['rest_url'] + creds['resources_path']
        #print("Rest host is ", creds['rest_url'], " resources_url is ", resources_url)
        try:
            response = requests.get(resources_url, auth=(username, password)).json()
        except:
            logger.exception("Error while querying url: " + resources_url)
            raise

        rest_api_url = response['streams_rest_url']

        # Give each view in the app the necessary information to connect to SWS.
        for view in app_topology.get_views():
            view.set_streams_context_config(
                {'username': username, 'password': password, 'rest_api_url': rest_api_url})


class _SubmitContextFactory:
    """
    ContextSubmitter:
        Responsible for performing the correct submission depending on a number of factors, including: the
        presence/absence of a streams install, the type of context, and whether the user seeks to retrieve data via rest
    """
    def __init__(self, app_topology, config=None, username=None, password=None, rest_api_url=None):
        self.app_topology = app_topology.graph
        self.config = config
        self.username = username
        self.password = password
        self.rest_api_url = rest_api_url

        if self.config is None:
            self.config = {}

        # deserialize vcap config before passing it off to Java
        if 'topology.service.vcap' in self.config:
            self.config['topology.service.vcap'] = json.loads(self.config['topology.service.vcap'])

    def get_submit_context(self, ctxtype):

        # If there is no streams install present, currently only REMOTE_BUILD_AND_SUBMIT is supported.
        streams_install = os.environ.get('STREAMS_INSTALL')
        if streams_install is None:
            if ctxtype == 'REMOTE_BUILD_AND_SUBMIT':
                return _RemoteBuildSubmitter(ctxtype, self.config, self.app_topology)
            elif ctxtype == 'TOOLKIT' or ctxtype == 'BUILD_ARCHIVE':
                return _BaseSubmitter(ctxtype, self.config, self.app_topology)
            else:
                raise UnsupportedContextException(ctxtype + " must be submitted when a streams install is present.")

        # Support for other contexts here.


# Used to delete the JSON file after it is no longer needed.
def _delete_json(fn):
    if os.path.isfile(fn):
        os.remove(fn)


# Used by a thread which polls a subprocess's stdout and writes it to stdout
def _print_process_stdout(process):
    try:
        while True:
            line = process.stdout.readline()
            if len(line) == 0:
                process.stdout.close()
                break
            line = line.decode("utf-8").strip()
            print(line)
    except:
        process.stdout.close()
        logger.exception("Error reading from process stdout")
        raise


# Used by a thread which polls a subprocess's stderr and writes it to stderr, until the sc compilation
# has begun.
def _print_process_stderr(process, fn):
    try:
        while True:
            line = process.stderr.readline()
            if len(line) == 0:
                process.stderr.close()
                break
            line = line.decode("utf-8").strip()
            print(line)
            if "com.ibm.streamsx.topology.internal.streams.InvokeSc getToolkitPath" in line:
                _delete_json(fn)
    except:
        process.stderr.close()
        logger.exception("Error reading from process stderr")
        raise


class UnsupportedContextException(Exception):
    """
    An exeption class for when something goes wrong with submitting using a particular context.
    """
    def __init__(self, msg):
        Exception.__init__(self, msg)
