# -*- coding: utf-8 -*-
#
# Copyright 2020 - Viktor Gal
# A partnership between École Polytechnique Fédérale de Lausanne (EPFL) and
# Eidgenössische Technische Hochschule Zürich (ETHZ).
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import pathlib
import json
import typing

import click
import pydotplus
import rdflib
import rdflib.tools.rdf2dot
from rdflib.tools.rdf2dot import LABEL_PROPERTIES

from pathlib import Path

from rdflib.tools import rdf2dot
from renku.core.models.cwl.annotation import Annotation
from renku.core.incubation.command import Command
from renku.core.plugins import hookimpl
from renku.core.models.provenance.provenance_graph import ProvenanceGraph
from renku.core.errors import RenkuException
from renku.core.management import LocalClient

from prettytable import PrettyTable
from lxml import etree

from aqsconverters.io import AQS_DIR, COMMON_DIR
from astropy.coordinates import SkyCoord, Angle
from dateutil import parser
import time

class AQS(object):
    def __init__(self, run):
        self.run = run

    @property
    def renku_aqs_path(self):
        """Return a ``Path`` instance of Renku AQS metadata folder."""        
        return Path(self.run.client.renku_home).joinpath(AQS_DIR).joinpath(COMMON_DIR)

    def load_model(self, path):
        """Load AQS reference file."""
        if path and path.exists():
            return json.load(path.open())
        return {}


@hookimpl
def process_run_annotations(run):
    """``process_run_annotations`` hook implementation."""
    aqs = AQS(run)

    #os.remove(os.path.join(aqs.renku_aqs_path, "site.py"))

    path = pathlib.Path("../sitecustomize.py")
    if path.exists():
        path.unlink()
    
    annotations = []

    print("process_run_annotations")

    if os.path.exists(aqs.renku_aqs_path):
        for p in aqs.renku_aqs_path.iterdir():
            if p.match("*json"):
                print(f"found json annotation: {p}")
                print(open(p).read())
        
            elif p.match("*jsonld"):
                print(f"found jsonLD annotation: {p}\n", json.dumps(json.load(p.open()), sort_keys=True, indent=4))

                # this will make annotations according to https://odahub.io/ontology/
                aqs_annotation = aqs.load_model(p)
                model_id = aqs_annotation["@id"]
                annotation_id = "{activity}/annotations/aqs/{id}".format(
                    activity=run._id, id=model_id
                )
                p.unlink()
                annotations.append(
                    Annotation(id=annotation_id, source="AQS plugin", body=aqs_annotation)
                )
    else:
        print("nothing to process in process_run_annotations")

    return annotations


@hookimpl
def pre_run(tool):
    # we print
    print(f"\033[31mhere we will prepare hooks for astroquery, tool given is {tool}\033[0m")    

    # TODO: where to get renku.client and dir?

    # TODO: how to write provide this to `tool`?
    fn = "../sitecustomize.py"

    print(f"\033[34msitecustomize.py as {fn}\033[0m")    

    open(fn, "w").write("""
print(f"\033[31menabling hooks for astroquery\033[0m")  

import aqsconverters.aq

aqsconverters.aq.autolog()
""")

    from astroquery.query import BaseQuery # ??


def _run_id(activity_id):
    return str(activity_id).split("/")[-1]


def _load_provenance_graph(client):
    if not client.provenance_graph_path.exists():
        raise RenkuException(
            """Provenance graph has not been generated!
Please run 'renku graph generate' to create the project's provenance graph
"""
        )
    return ProvenanceGraph.from_json(client.provenance_graph_path)


def _graph(revision, paths):
    # FIXME: use (revision, paths) filter
    cmd_result = Command().command(_load_provenance_graph).build().execute()

    provenance_graph = cmd_result.output
    provenance_graph.custom_bindings = {
        "aqs": "http://www.w3.org/ns/aqs#",
        "oa": "http://www.w3.org/ns/oa#",
        "xsd": "http://www.w3.org/2001/XAQSchema#",
    }

    return provenance_graph


def renku_context():
    ctx = click.get_current_context().ensure_object(LocalClient)
    return ctx


def _create_leaderboard(data, metric, format=None):
    leaderboard = PrettyTable()
    leaderboard.field_names = ["Run ID", "Module", "Query", metric]
    leaderboard.align["Module"] = "l"
    leaderboard.align["Query"] = "l"
    leaderboard.align[metric] = "r"
    for commit, v in data.items():
        if metric in v:
            v["query"].sort()
            leaderboard.add_row([commit, v["module"], v["query"], v[metric]])
    leaderboard.sortby = metric
    leaderboard.reversesort = True
    return leaderboard


@click.group()
def aqs():
    pass


@aqs.command()
@click.option(
    "--revision",
    default="HEAD",
    help="The git revision to generate the log for, default: HEAD",
)
@click.option("--format", default="ascii", help="Choose an output format.")
@click.option("--metric", default="accuracy", help="Choose metric for the leaderboard")
@click.argument("paths", type=click.Path(exists=False), nargs=-1)
def leaderboard(revision, format, metric, paths):
    """Leaderboard based on performance of astroquery requests"""
    graph = _graph(revision, paths)
    leaderboard = dict()

    # how to use ontology
    for r in graph.query(
        """SELECT DISTINCT ?a_object ?aq_module WHERE {{
        ?run <http://odahub.io/ontology#isRequestingAstroObject> ?a_object;
             <http://odahub.io/ontology#isUsing> ?aq_module .
        }}"""):

        print(r)


@aqs.command()
@click.option(
    "--revision",
    default="HEAD",
    help="The git revision to generate the log for, default: HEAD",
)
@click.option("--format", default="ascii", help="Choose an output format.")
@click.option(
    "--diff", nargs=2, help="Print the difference between two model revisions"
)
@click.argument("paths", type=click.Path(exists=False), nargs=-1)
def params(revision, format, paths, diff):
    """List the parameters of astroquery requests"""

    def _param_value(rdf_iteral):
        if not type(rdf_iteral) != rdflib.term.Literal:
            return rdf_iteral
        if rdf_iteral.isnumeric():
            return rdf_iteral.__str__()
        else:
            return rdf_iteral.toPython()

    graph = _graph(revision, paths)

    renku_path = renku_context().renku_path

    # model_params = dict()
    # how to use ontology
    output = PrettyTable()
    output.field_names = ["Run ID", "AstroQuery Module", "Astro Object"]
    output.align["Run ID"] = "l"

    # for the query_object
    query_where = """WHERE {{
        ?run <http://odahub.io/ontology#isUsing> ?aq_module ;
             <http://odahub.io/ontology#isRequestingAstroObject> ?a_object ;
             ^oa:hasBody/oa:hasTarget ?runId .
        
        OPTIONAL {{ ?run <http://purl.org/dc/terms/title> ?run_title . }}

        ?a_object <http://purl.org/dc/terms/title> ?a_object_name .
        
        ?aq_module <http://purl.org/dc/terms/title> ?aq_module_name .
        
        OPTIONAL {{ ?run <http://purl.org/dc/terms/title> ?run_title . }}

        ?run ?p ?o .

        }}"""

    invalid_entries = 0

    for r in graph.query(f"""
        SELECT DISTINCT ?run ?runId ?a_object ?a_object_name ?aq_module ?aq_module_name 
        {query_where}
        """):
        if " " in r.a_object:
            invalid_entries += 1
        else:
            output.add_row([
                _run_id(r.runId),
                r.aq_module_name,
                r.a_object_name
            ])
    print(output, "\n")

    # for the query_region
    query_where = """WHERE {{
        ?run <http://odahub.io/ontology#isUsing> ?aq_module ;
             <http://odahub.io/ontology#isRequestingAstroRegion> ?a_region ;
             ^oa:hasBody/oa:hasTarget ?runId .
        
        OPTIONAL {{ ?run <http://purl.org/dc/terms/title> ?run_title . }}

        ?a_region <http://purl.org/dc/terms/title> ?a_region_name .
        
        ?aq_module <http://purl.org/dc/terms/title> ?aq_module_name .
        
        OPTIONAL {{ ?run <http://purl.org/dc/terms/title> ?run_title . }}

        ?run ?p ?o .
    }}"""

    output = PrettyTable()
    output.field_names = ["Run ID", "AstroQuery Module", "Astro Region"]
    output.align["Run ID"] = "l"
    for r in graph.query(f"""
        SELECT DISTINCT ?run ?runId ?a_region ?a_region_name ?aq_module ?aq_module_name 
        {query_where}
        """):
        if " " in r.a_region:
            invalid_entries += 1
        else:
            output.add_row([
                _run_id(r.runId),
                r.aq_module_name,
                r.a_region_name
            ])

    print(output, "\n")
    if invalid_entries > 0:
        print("Some entries within the graph are not valid and therefore the store should be recreated", "\n")

    query_where = """WHERE {{
            {
                ?run <http://odahub.io/ontology#isUsing> ?aq_module ;
                     <http://odahub.io/ontology#isRequestingAstroObject> ?a_object ;
                     ^oa:hasBody/oa:hasTarget ?runId .
                 
                ?a_object <http://purl.org/dc/terms/title> ?a_object_name .
                
                ?aq_module <http://purl.org/dc/terms/title> ?aq_module_name .
                
                OPTIONAL {{ ?run <http://purl.org/dc/terms/title> ?run_title . }}
                
                ?run ?p ?o .

                FILTER (!CONTAINS(str(?a_object), " ")) .
            
            }
            UNION
            {
                ?run <http://odahub.io/ontology#isUsing> ?aq_module ;
                     <http://odahub.io/ontology#isRequestingAstroRegion> ?a_region ;
                     ^oa:hasBody/oa:hasTarget ?runId .
                
                ?a_region a ?a_region_type ; 
                    <http://purl.org/dc/terms/title> ?a_region_name ;
                    <http://odahub.io/ontology#isUsingSkyCoordinates> ?a_sky_coordinates ;
                    <http://odahub.io/ontology#isUsingRadius> ?a_radius .
                
                ?aq_module <http://purl.org/dc/terms/title> ?aq_module_name .

                OPTIONAL {{ ?run <http://purl.org/dc/terms/title> ?run_title . }}
                
                ?run ?p ?o .
            }
            }}"""

    r = graph.query(f"""
        CONSTRUCT {{
            ?run <http://odahub.io/ontology#isRequestingAstroObject> ?a_object .
            ?run <http://odahub.io/ontology#isRequestingAstroRegion> ?a_region .
            ?run <http://purl.org/dc/terms/title> ?run_title .
            ?run <http://odahub.io/ontology#isUsing> ?aq_module .
            ?run ?p ?o .
            
            ?a_region a ?a_region_type ; 
                <http://purl.org/dc/terms/title> ?a_region_name ;
                <http://odahub.io/ontology#isUsingSkyCoordinates> ?a_sky_coordinates ;
                <http://odahub.io/ontology#isUsingRadius> ?a_radius .
        }}
        {query_where}
        """)

    G = rdflib.Graph()
    G.parse(data=r.serialize(format="n3").decode(), format="n3")
    G.bind("oda", "http://odahub.io/ontology#")
    G.bind("odas", "https://odahub.io/ontology#")   # the same
    G.bind("local-renku", f"file://{renku_path}/") #??

    serial = G.serialize(format="n3").decode()

    print(serial)

    with open("subgraph.ttl", "w") as f:
        f.write(serial)


@aqs.command()
@click.option(
    "--revision",
    default="HEAD",
    help="The git revision to generate the log for, default: HEAD",
)
@click.option("--filename", default="graph.png", help="The filename of the output file image")
@click.option("--input-notebook", default=None, help="Input notebook to process")
@click.option("--no-oda-info", is_flag=True, help="Exclude oda related information in the output graph")
@click.argument("paths", type=click.Path(exists=False), nargs=-1)
def display(revision, paths, filename, no_oda_info, input_notebook):
    """Simple graph visualization """
    import io
    from IPython.display import display
    import pydotplus

    graph = _graph(revision, paths)

    renku_path = renku_context().renku_path

    query_where = build_query_where(input_notebook=input_notebook)
    query_construct = build_query_construct(input_notebook=input_notebook, no_oda_info=no_oda_info)

    query = f"""{query_construct}
        {query_where}
        """

    print("Before starting the query")
    t1 = time.perf_counter()
    r = graph.query(query)
    t2 = time.perf_counter()
    print("Query completed in %d !" % (t2 - t1))

    G = rdflib.Graph()
    G.parse(data=r.serialize(format="n3").decode(), format="n3")
    G.bind("oda", "http://odahub.io/ontology#")
    G.bind("odas", "https://odahub.io/ontology#") # the same
    G.bind("local-renku", f"file://{renku_path}/")

    extract_activity_start_time(G)

    if not no_oda_info:
        # process oda-related information (eg do the inferring)
        process_oda_info(G)

    action_node_dict = {}
    type_label_values_dict = {}
    args_default_value_dict = {}
    in_default_value_dict = {}
    out_default_value_dict = {}

    analyze_inputs(G, in_default_value_dict)
    analyze_arguments(G, action_node_dict, args_default_value_dict)
    analyze_outputs(G, out_default_value_dict)
    analyze_types(G, type_label_values_dict)

    clean_graph(G)

    stream = io.StringIO()
    rdf2dot.rdf2dot(G, stream, opts={display})
    pydot_graph = pydotplus.graph_from_dot_data(stream.getvalue())

    # list of edges and simple color change
    for edge in pydot_graph.get_edge_list():
        customize_edge(edge)

    for node in pydot_graph.get_nodes():
        customize_node(node, type_label_values_dict=type_label_values_dict)

    # final output write over the png image
    pydot_graph.write_png(filename)


def clean_graph(g):
    # remove not-needed triples
    g.remove((None, rdflib.URIRef('http://www.w3.org/ns/prov#hadPlan'), None))
    g.remove((None, rdflib.URIRef('http://purl.org/dc/terms/title'), None))
    g.remove((None, rdflib.URIRef('http://www.w3.org/ns/prov#qualifiedAssociation'), None))
    g.remove((None, rdflib.URIRef('http://www.w3.org/ns/oa#hasTarget'), None))
    g.remove((None, rdflib.URIRef('https://swissdatasciencecenter.github.io/renku-ontology#position'), None))
    g.remove((None, rdflib.URIRef('https://swissdatasciencecenter.github.io/renku-ontology#hasArguments'), None))
    # remove all the type triples
    g.remove((None, rdflib.RDF.type, None))


def analyze_types(g, type_label_values_dict):
    # analyze types
    types_list = g[:rdflib.RDF.type]
    for s, o in types_list:
        o_qname = g.compute_qname(o)
        s_label = label(s, g)
        type_label_values_dict[s_label] = o_qname[2]


def analyze_outputs(g, out_default_value_dict):
    # analyze outputs
    outputs_list = g[:rdflib.URIRef('https://swissdatasciencecenter.github.io/renku-ontology#hasOutputs')]
    for s, o in outputs_list:
        s_label = label(s, g)
        if s_label not in out_default_value_dict:
            out_default_value_dict[s_label] = []
        output_obj_list = list(g[o:rdflib.URIRef('http://schema.org/defaultValue')])
        if len(output_obj_list) == 1:
            # get file extension
            file_extension = os.path.splitext(output_obj_list[0])[1][1:]

            if file_extension is not None:
                if file_extension in ['jpeg', 'jpg', 'png', 'gif', 'bmp']:
                    # removing old type, and assigning a new specific one
                    g.remove((o, rdflib.RDF.type, None))
                    g.add((o,
                           rdflib.RDF.type,
                           rdflib.URIRef("https://swissdatasciencecenter.github.io/renku-ontology#CommandOutputImage")))
                if file_extension in ['fits']:
                    # removing old type, and assigning a new specific one
                    g.remove((o, rdflib.RDF.type, None))
                    g.add((o,
                           rdflib.RDF.type,
                           rdflib.URIRef("https://swissdatasciencecenter.github.io/renku-ontology#CommandOutputFitsFile")))
                else:
                    if file_extension == 'ipynb':
                        g.remove((o, rdflib.RDF.type, None))
                        g.add((o,
                               rdflib.RDF.type,
                               rdflib.URIRef(
                                   "https://swissdatasciencecenter.github.io/renku-ontology#CommandOutputNotebook")))

            out_default_value_dict[s_label].append(output_obj_list[0])


def analyze_arguments(g, action_node_dict, args_default_value_dict):
    # analyze arguments (and join them all together)
    args_list = g[:rdflib.URIRef('https://swissdatasciencecenter.github.io/renku-ontology#hasArguments')]
    for s, o in args_list:
        s_label = label(s, g)
        if s_label not in action_node_dict:
            action_node_dict[s_label] = s
        if s_label not in args_default_value_dict:
            args_default_value_dict[s_label] = []
        arg_obj_list = g[o:rdflib.URIRef('http://schema.org/defaultValue')]
        for arg_o in arg_obj_list:
            position_o = list(g[o:rdflib.URIRef('https://swissdatasciencecenter.github.io/renku-ontology#position')])
            if len(position_o) == 1:
                args_default_value_dict[s_label].append((arg_o.n3().strip('\"'), position_o[0].value))
                g.remove((o, rdflib.URIRef('http://schema.org/defaultValue'), arg_o))
    # infer isArgumentOf property for each action, this implies the creation of the new CommandParameter nodes
    # with the related defaultValue
    for action in args_default_value_dict.keys():
        arg_pos_list = args_default_value_dict[action].copy()
        # order according their position
        arg_pos_list.sort(key=lambda arg_tuple: arg_tuple[1])
        iter_arg_pos_list = iter(arg_pos_list)
        for x, y in zip(iter_arg_pos_list, iter_arg_pos_list):
            # create the node
            # TODO id needs to be properly assigned! now the name of the parameter is used
            node_args = rdflib.URIRef("https://github.com/plans/84d9b437-4a55-4573-9aa3-4669ff641f1b/parameters/"
                                      + x[0].replace(" ", "_") + "_" + y[0].replace(" ", "_"))
            # link it to the action node
            g.add((node_args,
                   rdflib.URIRef('https://swissdatasciencecenter.github.io/renku-ontology#isArgumentOf'),
                   action_node_dict[action]))
            # value for the node args
            g.add((node_args,
                   rdflib.URIRef('http://schema.org/defaultValue'),
                   rdflib.Literal((x[0] + " " + y[0]).strip())))
            # type for the node args
            # TODO to discuss what the best approach to assign the type case is:
            # to create a node with a dedicated type inferred from the arguments
            # G.add((node_args,
            #        rdflib.RDF.type,
            #        rdflib.URIRef("https://swissdatasciencecenter.github.io/renku-ontology#" + x[0])))
            # or still create a new CommandParameter and use the defaultValue information
            g.add((node_args,
                   rdflib.RDF.type,
                   rdflib.URIRef("https://swissdatasciencecenter.github.io/renku-ontology#CommandParameter")))


def label(x, g):
    for labelProp in LABEL_PROPERTIES:
        l = g.value(x, labelProp)
        if l:
            return l
    try:
        return g.namespace_manager.compute_qname(x)[2]
    except:
        return x


def analyze_inputs(g, in_default_value_dict):
    # analyze inputs
    inputs_list = g[:rdflib.URIRef('https://swissdatasciencecenter.github.io/renku-ontology#hasInputs')]
    for s, o in inputs_list:
        s_label = label(s, g)
        if s_label not in in_default_value_dict:
            in_default_value_dict[s_label] = []
        input_obj_list = g[o]
        for input_p, input_o in input_obj_list:
            if input_p.n3() == "<http://schema.org/defaultValue>":
                in_default_value_dict[s_label].append(input_o.n3().strip('\"'))
        # infer isInputOf property
        g.add((o, rdflib.URIRef('https://swissdatasciencecenter.github.io/renku-ontology#isInputOf'), s))
    g.remove((None, rdflib.URIRef('https://swissdatasciencecenter.github.io/renku-ontology#hasInputs'), None))


def extract_activity_start_time(g):
    # extract the info about the activity start time
    # get the activities and extract for each the startedTime into, and attach it to the related Action
    start_time_activity_list = g[:rdflib.URIRef('http://www.w3.org/ns/prov#startedAtTime')]
    for activity_node, activity_start_time in start_time_activity_list:
        # get the association and then the action
        qualified_association_list = g[activity_node:rdflib.URIRef('http://www.w3.org/ns/prov#qualifiedAssociation')]
        for association_node in qualified_association_list:
            plan_list = g[association_node:rdflib.URIRef('http://www.w3.org/ns/prov#hadPlan')]
            for plan_node in plan_list:
                g.add((plan_node, rdflib.URIRef('http://www.w3.org/ns/prov#startedAtTime'), activity_start_time))
                g.remove((activity_node, rdflib.URIRef('http://www.w3.org/ns/prov#startedAtTime'), activity_start_time))


def process_oda_info(g):
    # find a way to the action form the Run by extracting activity qualified association
    run_target_list = g[:rdflib.URIRef('http://www.w3.org/ns/oa#hasTarget')]
    for run_node, activity_node in run_target_list:
        # run_node is the run, act_node is the activity
        qualified_association_list = g[activity_node:rdflib.URIRef('http://www.w3.org/ns/prov#qualifiedAssociation')]
        run_title = None
        run_title_list = list(g[run_node:rdflib.URIRef('http://purl.org/dc/terms/title')])
        if len(run_title_list) == 1:
            run_title = run_title_list[0]
        for association_node in qualified_association_list:
            plan_list = g[association_node:rdflib.URIRef('http://www.w3.org/ns/prov#hadPlan')]
            for plan_node in plan_list:
                # we inferred a connection from the run to an action
                # and we can now infer the request of a certain astroObject and the usage of a certain module
                used_module_list = list(g[run_node:rdflib.URIRef('http://odahub.io/ontology#isUsing')])
                # one module in use per annotation and one requested AstroObject/AstroRegion
                module_node = used_module_list[0]
                # query_object
                requested_astroObject_list = list(
                    g[run_node:rdflib.URIRef('http://odahub.io/ontology#isRequestingAstroObject')])
                if len(requested_astroObject_list) > 0:
                    # if run_node is of the type query_object
                    # for module_node in used_module_list:
                    g.add((module_node, rdflib.URIRef('http://odahub.io/ontology#isUsedDuring'),
                           plan_node))
                    # for astroObject_node in requested_astroObject_list:
                    astroObject_node = requested_astroObject_list[0]
                    g.add((module_node, rdflib.URIRef('http://odahub.io/ontology#requestsAstroObject'),
                           astroObject_node))
                g.remove((run_node,
                          rdflib.URIRef('http://odahub.io/ontology#isRequestingAstroObject'),
                          None))
                # query_region
                requested_astroRegion_list = list(
                    g[run_node:rdflib.URIRef('http://odahub.io/ontology#isRequestingAstroRegion')])
                if len(requested_astroRegion_list) > 0:
                    # if run_node is of the type query_region
                    # for module_node in used_module_list:
                    g.add((module_node, rdflib.URIRef('http://odahub.io/ontology#isUsedDuring'),
                           plan_node))
                    # for astroObject_node in requested_astroObject_list:
                    astroRegion_node = requested_astroRegion_list[0]
                    g.add((module_node, rdflib.URIRef('http://odahub.io/ontology#requestsAstroRegion'),
                           astroRegion_node))
                    # sky coordinates info (if found, perhaps some for old query_region none was stored)
                    sky_coordinates_list = list(
                        g[astroRegion_node:rdflib.URIRef('http://odahub.io/ontology#isUsingSkyCoordinates')])
                    if len(sky_coordinates_list) == 1:
                        sky_coordinates_node = sky_coordinates_list[0]
                        sky_coordinates_node_title = list(
                            g[sky_coordinates_node:rdflib.URIRef('http://purl.org/dc/terms/title')])
                        if len(sky_coordinates_node_title) == 1:
                            # define an astropy SkyCoord object
                            coords = sky_coordinates_node_title[0].value.split(" ")
                            sky_coord_obj = SkyCoord(coords[0], coords[1], unit='degree')
                            sky_coord_obj_default_value = str(sky_coord_obj.dec.deg) + " " + str(sky_coord_obj.ra.deg) \
                                                          + " unit=deg"
                            g.add((sky_coordinates_node, rdflib.URIRef('http://schema.org/defaultValue'),
                                   rdflib.Literal(sky_coord_obj_default_value)))
                    # radius info (if found, perhaps some for old query_region none was stored)
                    radius_list = list(
                        g[astroRegion_node:rdflib.URIRef('http://odahub.io/ontology#isUsingRadius')])
                    if len(radius_list) == 1:
                        radius_node = radius_list[0]
                        radius_node_title = list(
                            g[radius_node:rdflib.URIRef('http://purl.org/dc/terms/title')])
                        if len(radius_node_title) == 1:
                            # define an astropy Angle object
                            radius_obj = Angle(radius_node_title[0].value)
                            radius_obj_default_value = str(radius_obj.arcmin) + " unit=arcmin"
                            g.add((radius_node, rdflib.URIRef('http://schema.org/defaultValue'),
                                   rdflib.Literal(radius_obj_default_value)))
                g.remove((run_node,
                          rdflib.URIRef('http://odahub.io/ontology#isRequestingAstroRegion'),
                          None))
                g.remove((run_node,
                          rdflib.URIRef('http://odahub.io/ontology#isUsing'),
                          None))


def customize_edge(edge: typing.Union[pydotplus.Edge]):
    if 'label' in edge.obj_dict['attributes']:
        edge_html = etree.fromstring(edge.obj_dict['attributes']['label'][1:-1])
        # simple color code
        # those two are not relevant at the moment since new predicates have been infered
        # if edge_html.text == 'oda:isRequestingAstroObject':
        #     edge.obj_dict['attributes']['color'] = '#2986CC'
        # if edge_html.text == 'oda:isUsing':
        #     edge.obj_dict['attributes']['color'] = '#53D06A'
        # TODO remove first part of the label ?
        edge_html.text = edge_html.text.split(":")[1]
        edge.set_label('< ' + etree.tostring(edge_html, encoding='unicode') + ' >')


def customize_node(node: typing.Union[pydotplus.Node],
                   type_label_values_dict=None
                   ):
    if 'label' in node.obj_dict['attributes']:
        # parse the whole node table into a lxml object
        table_html = etree.fromstring(node.get_label()[1:-1])
        tr_list = table_html.findall('tr')

        # modify the first row, hence the title of the node, and then all the rest
        id_node = None
        td_list_first_row = tr_list[0].findall('td')
        if td_list_first_row is not None:
            td_list_first_row[0].attrib.pop('bgcolor')
            b_element_title = td_list_first_row[0].findall('B')
            if b_element_title is not None and b_element_title[0].text in type_label_values_dict:
                id_node = b_element_title[0].text
            if id_node is not None:
                # change title of the node
                if type_label_values_dict[b_element_title[0].text] != 'CommandParameter':
                    b_element_title[0].text = type_label_values_dict[b_element_title[0].text]
                if b_element_title[0].text.startswith('CommandOutput') and \
                        b_element_title[0].text != 'CommandOutput':
                    b_element_title[0].text = b_element_title[0].text[13:]
                # apply styles (shapes, colors etc etc)
                table_html.attrib['border'] = '0'
                table_html.attrib['cellborder'] = '0'
                node.set_style("filled")
                node.set_shape("box")
                # color and shape change
                #1B81FB
                if type_label_values_dict[id_node] == 'Action':
                    node.set_shape("diamond")
                    node.set_color("#D5C15D")
                elif type_label_values_dict[id_node] == 'CommandOutput':
                    node.set_color("#FFFF00")
                elif type_label_values_dict[id_node] == 'CommandOutputImage' or \
                        type_label_values_dict[id_node] == 'CommandOutputFitsFile':
                    table_html.attrib['border'] = '1'
                    node.set_color("#FFFFFF")
                elif type_label_values_dict[id_node] == 'CommandOutputNotebook':
                    node.set_color("#DBA3BC")
                elif type_label_values_dict[id_node] == 'CommandInput':
                    node.set_color("#DBA3BC")
                elif type_label_values_dict[id_node] == 'CommandParameter':
                    node.set_color("#6262be")
                elif type_label_values_dict[id_node] == 'AstroqueryModule':
                    node.set_shape("ellipse")
                    node.set_color("#00CC00")
                elif type_label_values_dict[id_node] == 'AstrophysicalObject':
                    node.set_shape("ellipse")
                    node.set_color("#6262be")
                elif type_label_values_dict[id_node] == 'AstrophysicalRegion':
                    node.set_shape("ellipse")
                    node.set_color("#6262bf")
                elif type_label_values_dict[id_node] == 'Angle' or \
                        type_label_values_dict[id_node] == 'SkyCoordinates':
                    node.set_color("#1B81FB")
                # remove top row for the cells Action and CommandInput
                if type_label_values_dict[id_node] == 'CommandInput' or \
                        type_label_values_dict[id_node] == 'Action':
                    table_html.remove(tr_list[0])
                # remove not needed long id information
                table_html.remove(tr_list[1])
                # remove not-needed information in the output tree nodes (eg defaultValue text, position value)
                for tr in tr_list:
                    list_td = tr.findall('td')
                    if len(list_td) == 2:
                        list_left_column_element = list_td[0].text.split(':')
                        # remove left side text (eg defaultValue)
                        tr.remove(list_td[0])
                        if 'align' in list_td[1].keys():
                            list_td[1].attrib['align'] = 'center'
                            list_td[1].attrib['colspan'] = '2'
                        # special case default_value table_row
                        if 'defaultValue' in list_left_column_element and \
                                type_label_values_dict[id_node] == 'CommandParameter':
                            list_args_commandParameter = list_td[1].text[1:-1].split(' ')
                            if b_element_title is not None and b_element_title[0].text in type_label_values_dict:
                                b_element_title[0].text = list_args_commandParameter[0]
                                list_td[1].text = '"' + ' '.join(list_args_commandParameter[1:]) + '"'
                        if 'startedAtTime' in list_left_column_element:
                            # TODO to improve and understand how to parse xsd:dateTime time
                            parsed_startedAt_time = parser.parse(list_td[1].text.replace('^^xsd:dateTime', '')[1:-1])
                            # create an additional row to attach at the bottom, so that time is always at the bottom
                            bottom_table_row = etree.Element('tr')
                            time_td = etree.Element('td')
                            time_td.attrib['align'] = 'center'
                            time_td.attrib['colspan'] = '2'
                            time_td.text = parsed_startedAt_time.strftime('%Y-%m-%d %H:%M:%S')
                            bottom_table_row.append(time_td)
                            tr.remove(list_td[1])
                            table_html.remove(tr)
                            table_html.append(bottom_table_row)

                        # remove trailing and leading double quotes
                        list_td[1].text = list_td[1].text[1:-1]
                        # bold text in case of action
                        if type_label_values_dict[id_node] == 'Action' and \
                                 'command' in list_left_column_element:
                            bold_text_element = etree.Element('B')
                            bold_text_element.text = list_td[1].text
                            list_td[1].append(bold_text_element)
                            list_td[1].text = ""
                        # italic ad bold in case of input
                        if type_label_values_dict[id_node] == 'CommandInput':
                            bold_text_element = etree.Element('B')
                            italic_text_element = etree.Element('I')
                            italic_text_element.text = list_td[1].text
                            bold_text_element.append(italic_text_element)
                            list_td[1].append(bold_text_element)
                            list_td[1].text = ""

            # serialize back the table html
            node.obj_dict['attributes']['label'] = '< ' + etree.tostring(table_html, encoding='unicode') + ' >'


def build_query_where(input_notebook: str = None):
    # TODO still to fully verify that the results is the expected one
    if input_notebook is not None:
        query_where = f"""WHERE {{
            {{
            ?action a <http://schema.org/Action> ; 
                <https://swissdatasciencecenter.github.io/renku-ontology#hasInputs> ?actionParamInput ;
                <https://swissdatasciencecenter.github.io/renku-ontology#command> ?actionCommand ;
                ?has ?actionParam .
    
            ?actionParamInput a ?actionParamInputType ;
                <http://schema.org/defaultValue> '{input_notebook}' .
                
            FILTER ( ?actionParamInputType = <https://swissdatasciencecenter.github.io/renku-ontology#CommandInput>) .
            
            FILTER (?has IN (<https://swissdatasciencecenter.github.io/renku-ontology#hasArguments>, 
                <https://swissdatasciencecenter.github.io/renku-ontology#hasOutputs>
                ))
    
            ?actionParam a ?actionParamType ;
                <http://schema.org/defaultValue> ?actionParamValue .
    
            FILTER ( ?actionParamType IN (<https://swissdatasciencecenter.github.io/renku-ontology#CommandOutput>,
                                        <https://swissdatasciencecenter.github.io/renku-ontology#CommandParameter>)
                                        ) .
        """
    else:
        query_where = """WHERE {
            {
                ?action a <http://schema.org/Action> ;
                    <https://swissdatasciencecenter.github.io/renku-ontology#command> ?actionCommand ;
                    ?has ?actionParam .
    
                FILTER (?has IN (<https://swissdatasciencecenter.github.io/renku-ontology#hasArguments>,
                    <https://swissdatasciencecenter.github.io/renku-ontology#hasOutputs>,
                    <https://swissdatasciencecenter.github.io/renku-ontology#hasInputs>
                    ))
    
                ?actionParam a ?actionParamType ;
                    <http://schema.org/defaultValue> ?actionParamValue .
    
                FILTER ( ?actionParamType IN (<https://swissdatasciencecenter.github.io/renku-ontology#CommandOutput>,
                                        <https://swissdatasciencecenter.github.io/renku-ontology#CommandParameter>,
                                        <https://swissdatasciencecenter.github.io/renku-ontology#CommandInput>)
                                        ) .
        """

    query_where = query_where + """
                OPTIONAL { ?actionParam <https://swissdatasciencecenter.github.io/renku-ontology#position> ?actionPosition } .
            }
            
            {
                ?activity a ?activityType ;
                    <http://www.w3.org/ns/prov#startedAtTime> ?activityTime ;
                    <https://swissdatasciencecenter.github.io/renku-ontology#parameter> ?parameter_value ;
                    <http://www.w3.org/ns/prov#qualifiedAssociation> ?activity_qualified_association .
                    
                ?activity_qualified_association <http://www.w3.org/ns/prov#hadPlan> ?action .
            
                {
                    ?run <http://odahub.io/ontology#isUsing> ?aq_module ;
                         <http://odahub.io/ontology#isRequestingAstroObject> ?a_object ;
                         a ?run_rdf_type ;
                         ^oa:hasBody/oa:hasTarget ?runId ;
                         ^oa:hasBody/oa:hasTarget ?activity .
                    
                    ?a_object <http://purl.org/dc/terms/title> ?a_object_name ;
                        a ?a_obj_rdf_type .
        
                    ?aq_module <http://purl.org/dc/terms/title> ?aq_module_name ;
                        a ?aq_mod_rdf_type .
                    
                    OPTIONAL {{ ?run <http://purl.org/dc/terms/title> ?run_title . }}
                    
                    ?run ?p ?o .
    
                    FILTER (!CONTAINS(str(?a_object), " ")) .
                }
                UNION
                {
                    ?run <http://odahub.io/ontology#isUsing> ?aq_module ;
                         <http://odahub.io/ontology#isRequestingAstroRegion> ?a_region ;
                         a ?run_rdf_type ;
                         ^oa:hasBody/oa:hasTarget ?runId ;
                         ^oa:hasBody/oa:hasTarget ?activity .
                    
                    ?a_region a ?a_region_type ; 
                        <http://purl.org/dc/terms/title> ?a_region_name ;
                        <http://odahub.io/ontology#isUsingSkyCoordinates> ?a_sky_coordinates ;
                        <http://odahub.io/ontology#isUsingRadius> ?a_radius .
                    
                    ?aq_module a ?aq_mod_rdf_type ;
                        <http://purl.org/dc/terms/title> ?aq_module_name .
                         
                    ?a_sky_coordinates a ?a_sky_coordinates_type ;
                        <http://purl.org/dc/terms/title> ?a_sky_coordinates_name .
                        
                    ?a_radius a ?a_radius_type ;
                        <http://purl.org/dc/terms/title> ?a_radius_name .
    
                    OPTIONAL {{ ?run <http://purl.org/dc/terms/title> ?run_title . }}
                    
                    ?run ?p ?o .
                }
            }
        }
        """

    return query_where


def build_query_construct(input_notebook: str = None, no_oda_info=False):
    if input_notebook is not None:
        query_construct_action = f"""
                ?action a <http://schema.org/Action> ;
                    <https://swissdatasciencecenter.github.io/renku-ontology#command> ?actionCommand ;
                    <https://swissdatasciencecenter.github.io/renku-ontology#hasInputs> ?actionParamInput ;
                    ?has ?actionParam .

                ?actionParamInput a ?actionParamInputType ;
                    <http://schema.org/defaultValue> '{input_notebook}' .

                ?actionParam a ?actionParamType ;
                    <https://swissdatasciencecenter.github.io/renku-ontology#position> ?actionPosition ;
                    <http://schema.org/defaultValue> ?actionParamValue .
        """
    else:
        query_construct_action = """
                ?action a <http://schema.org/Action> ;
                    <https://swissdatasciencecenter.github.io/renku-ontology#command> ?actionCommand ;
                    ?has ?actionParam .

                ?actionParam a ?actionParamType ;
                    <https://swissdatasciencecenter.github.io/renku-ontology#position> ?actionPosition ;
                    <http://schema.org/defaultValue> ?actionParamValue .
        """
    # add time activity information
    query_construct_action += """
            ?activity a ?activityType ;
                <http://www.w3.org/ns/prov#startedAtTime> ?activityTime ;
                <http://www.w3.org/ns/prov#qualifiedAssociation> ?activity_qualified_association .
                
            ?activity_qualified_association <http://www.w3.org/ns/prov#hadPlan> ?action .
    """

    query_construct_oda_info = ""
    if not no_oda_info:
        query_construct_oda_info += """
                ?run <http://odahub.io/ontology#isRequestingAstroObject> ?a_object ;
                    <http://odahub.io/ontology#isRequestingAstroRegion> ?a_region ;
                    <http://purl.org/dc/terms/title> ?run_title ;
                    <http://odahub.io/ontology#isUsing> ?aq_module ;
                    oa:hasTarget ?activity ;
                    a ?run_rdf_type .

                ?a_object <https://odahub.io/ontology#AstroObject> ?a_object_name ;
                    a ?a_obj_rdf_type .
                    
                ?a_region a ?a_region_type ; 
                    <http://purl.org/dc/terms/title> ?a_region_name ;
                    <http://odahub.io/ontology#isUsingSkyCoordinates> ?a_sky_coordinates ;
                    <http://odahub.io/ontology#isUsingRadius> ?a_radius .
                    
                ?a_sky_coordinates a ?a_sky_coordinates_type ;
                    <http://purl.org/dc/terms/title> ?a_sky_coordinates_name .
                    
                ?a_radius a ?a_radius_type ;
                    <http://purl.org/dc/terms/title> ?a_radius_name .

                ?aq_module <https://odahub.io/ontology#AQModule> ?aq_module_name ;
                    a ?aq_mod_rdf_type .
            """

    query_construct = f"""CONSTRUCT {{
                {query_construct_action}
                {query_construct_oda_info}
            }}"""

    return query_construct
