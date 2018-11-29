"""
    assignment.py

    Contains dataclass routines for tracking assignments
    in broadband spectra.
"""

from dataclasses import dataclass, field
from lmfit.models import GaussianModel
from typing import List, Dict
from IPython.display import display, HTML
import numpy as np
import pandas as pd
import os
from shutil import rmtree
from periodictable import formula
from plotly import graph_objs as go

from . import analysis
from pyspectools import routines


@dataclass
class Assignment:
    """
        DataClass for handling assignments.
        There should be sufficient information to store a
        line assignment and reproduce it later in a form
        that is both machine and human readable.

        parameters:
        ------------------
        name - str representing IUPAC/common name
        formula - str representing chemical formula
        smiles - str representing SMILES code (specific!)
        frequency - float for observed frequency
        intensity - float for observed intensity
        peak_id - int for peak id from experiment
        uline - bool flag to signal whether line is identified or not
        composition - list-like with string corresponding to experimental
                      chemical composition. SMILES syntax.
        v_qnos - list with quantum numbers for vibrational modes. Index
                 corresponds to mode, and int value to number of quanta.
                 Length should be equal to 3N-6.
        experiment - int for experiment ID
        fit - dict-like containing the fitted parameters and model
    """
    name: str = ""
    smiles: str = ""
    formula: str = ""
    frequency: float = 0.0
    intensity: float = 0.0
    peak_id: int = 0
    experiment: int = 0
    uline: bool = False
    composition: List[str] = field(default_factory = list)
    v_qnos: List[int] = field(default_factory = list)
    r_qnos: str = ""
    fit: Dict = field(default_factory = dict)
    ustate_energy: float = 0.0
    weighting: float = 0.0

    def __eq__(self, other):
        """ Dunder method for comparing molecules.
            This method is simply a shortcut to see if
            two molecules are the same based on their
            SMILES code.
        """
        return self.smiles == other

    def __str__(self):
        return f"{self.name}, {self.frequency}"

    def to_file(self, filepath, format="yaml"):
        """ Method to dump data to YAML format.
            Extensions are automatically decided, but
            can also be supplied.

            parameters:
            --------------------
            filepath - str path to yaml file
            format - str denoting the syntax used for dumping.
                     Defaults to YAML.
        """
        if "." not in filepath:
            if format == "json":
                filepath+=".json"
            else:
                filepath+=".yml"
        if format == "json":
            writer = routines.dump_json
        else:
            writer = routines.dump_yaml
        writer(filepath, self.__dict__)

    def get_spectrum(self, x):
        """ Generate a synthetic peak by supplying
            the x axis for a particular spectrum. This method
            assumes that some fit parameters have been determined
            previously.

            parameters:
            ----------------
            x - 1D array with frequencies of experiment

            returns:
            ----------------
            y - 1D array of synthetic Gaussian spectrum
        """
        model = GaussianModel()
        params = model.make_params()
        params.update(self.fit)
        y = model.eval(params, x=x)
        return y

    @classmethod
    def from_dict(obj, data_dict):
        """ Method for generating an Assignment object
            from a dictionary. All this method does is
            unpack a dictionary into the __init__ method.

            parameters:
            ----------------
            data_dict - dict with DataClass fields

            returns:
            ----------------
            Assignment object
        """
        assignment_obj = obj(**data_dict)
        return assignment_obj

    @classmethod
    def from_yml(obj, yaml_path):
        """ Method for initializing an Assignment object
            from a YAML file.

            parameters:
            ----------------
            yaml_path - str path to yaml file

            returns:
            ----------------
            Assignment object
        """
        yaml_dict = routines.read_yaml(yaml_path)
        assignment_obj = obj(**yaml_dict)
        return assignment_obj

    @classmethod
    def from_json(obj, json_path):
        """ Method for initializing an Assignment object
            from a JSON file.

            parameters:
            ----------------
            json_path - str path to json file

            returns:
            ----------------
            Assignment object
        """
        json_dict = routines.read_json(json_path)
        assignment_obj = obj(**json_dict)
        return assignment_obj


@dataclass
class Session:
    """ DataClass for a Session, which simply holds the
        experiment ID, composition, and guess temperature
    """
    experiment: int
    composition: List[str] = field(default_factory = list)
    temperature: float = 4.0


class AssignmentSession:
    """ Class for managing a session of assigning molecules
        to a broadband spectrum.

        Wraps some high level functionality from the analysis
        module so that this can be run reproducibly in a jupyter
        notebook.
    """
    def __init__(
            self, exp_dataframe, experiment, composition, temperature=4.0,
            freq_col="Frequency", int_col="Intensity"):
        """ Initialize a AssignmentSession with a pandas dataframe
            corresponding to a spectrum.

            description of data:
            -------------------------
            exp_dataframe - pandas dataframe with observational data in frequency/intensity
            experiment - int ID for the experiment
            composition - list of str corresponding to experiment composition
            freq_col - optional arg specifying the name for the frequency column
            int_col - optional arg specifying the name of the intensity column
        """
        # Make folders for organizing output
        folders = ["assignment_objs", "queries", "sessions", "clean", "reports"]
        for folder in folders:
            if os.path.isdir(folder) is False:
                os.mkdir(folder)
        # Initialize a Session dataclass
        self.session = Session(experiment, composition, temperature)
        self.data = exp_dataframe
        self.assignments = list()
        self.ulines = list()
        # Default settings for columns
        if freq_col not in self.data.columns:
            self.freq_col = self.data.columns[0]
        else:
            self.freq_col = freq_col
        if int_col not in self.data.columns:
            self.int_col = self.data.columns[1]
        else:
            self.int_col = int_col

    def find_peaks(self, threshold=0.015):
        """ Wrap peakutils method for detecting peaks.

            parameters:
            ---------------
            threshold - peak detection threshold

            returns:
            ---------------
            peaks_df - dataframe containing peaks
        """
        peaks_df = analysis.peak_find(
            self.data,
            col=self.int_col,
            thres=threshold
           )
        self.peaks = peaks_df
        return peaks_df

    def splat_assign_spectrum(self, auto=False):
        """ Function that will provide an "interface" for interactive
            line assignment in a notebook environment.

            Basic functionality is looping over a series of peaks,
            which will query splatalogue for known transitions in the
            vicinity. If the line is known in Splatalogue, it will
            throw it into an Assignment object and flag it as known.
            Conversely, if it's not known in Splatalogue it will defer
            assignment, flagging it as unassigned and dumping it into
            the `uline` attribute.
        """
        if hasattr(self, "peaks") is False:
            print("Peak detection not run; running with default settings.")
            self.find_peaks()

        selected_session = {
            key: self.session.__dict__[key] for key in self.session.__dict__ if key != "temperature"
            }
        for index, row in self.peaks.iterrows():
            frequency = row[self.freq_col]
            # Call splatalogue API to search for frequency
            splat_df = analysis.search_center_frequency(frequency)
            # Set up a Assignment object, taking on the
            # specific details about the line as well as
            # inheriting the experimental details
            ass_obj = Assignment(
                frequency=frequency,
                intensity=row[self.int_col],
                peak_id=index,
                **selected_session
                )
            # Filter out lines that are way too unlikely on grounds of temperature
            splat_df = splat_df.loc[splat_df["E_U (K)"] <= self.session.temperature * 2.]
            # Filter out quack elemental compositions
            for index, row in splat_df.iterrows():
                # Convert the string into a chemical formula object
                try:
                    clean_formula = row["Species"].split("v=")[0]
                    for prefix in ["l-", "c-"]:
                        clean_formula = clean_formula.replace(prefix, "")
                    formula_obj = formula(clean_formula)
                    # Check if proposed molecule contains atoms not
                    # expected in composition
                    comp_check = all(
                        str(atom) in self.session.composition for atom in formula_obj.atoms
                        )
                    if comp_check is False:
                        # If there are crazy things in the mix, forget about it
                        print("Molecule " + clean_formula + " rejected.")
                        splat_df.drop(index, inplace=True)
                except:
                    print("Could not parse molecule " + clean_formula + " rejected.")
                    splat_df.drop(index, inplace=True)
            nitems = len(splat_df)

            if nitems > 0:
                # if there are splatalogue entries that have turned up
                splat_df["Deviation"] = np.abs(
                        splat_df["Combined"] - frequency
                        )
                # Weight by the deviation and state temperature - higher
                # weight is most likely.
                # Higher temperature lines and larger deviation mean the
                splat_df["Weighting"] = (1. / splat_df["Deviation"]) * (10**splat_df["CDMS/JPL Intensity"])
                splat_df["Weighting"]/=splat_df["Weighting"].max()
                # Sort by obs-calc
                splat_df.sort_values(["Weighting"], ascending=True)
                # Reindex based on distance from prediction
                splat_df.index = np.arange(len(splat_df))
                display(HTML(splat_df.to_html()))
                try:
                    print("Observed frequency is {:,.4f}".format(frequency))
                    if auto is False:
                        # If not automated, we need a human to look at frequencies
                        # Print the dataframe for notebook viewing
                        splat_index = int(
                            input(
                                "Please choose an assignment index: 0 - " + str(nitems - 1)
                                )
                            )
                    else:
                        # If automated, choose closest frequency
                        splat_index = 0

                    ass_df = splat_df.iloc[[splat_index]]
                    splat_df.to_csv(
                        "queries/{0}-{1}.csv".format(self.session.experiment, index), index=False
                        )
                    ass_obj.assigned = True
                    ass_obj.name = ass_df["Chemical Name"][0]
                    ass_obj.formula = ass_df["Species"][0]
                    ass_obj.r_qnos = ass_df["Resolved QNs"][0]
                    ass_obj.ustate_energy = ass_df["E_U (K)"][0]
                    ass_obj.weighting = ass_df["Weighting"][0]
                    print(ass_obj.name + " was assigned.")
                    # Perform a Voigt profile fit
                    print("Attempting to fit line profile...")
                    fit_results = analysis.fit_line_profile(
                        self.data,
                        frequency
                        )
                    # Pass the fitted parameters into Assignment object
                    ass_obj.fit.update(fit_results.best_values)
                    # Need support to convert common name to SMILES
                    self.assignments.append(ass_obj)
                except ValueError:
                    # If nothing matches, throw it into the U-line
                    # pile.
                    print("Deferring assignment")
                    line_dict.assigned = False
                    self.ulines.append(ass_obj)
            else:
                # Throw into U-line pile if no matches at all
                print("No species known for {:,.3f}".format(frequency))
                self.ulines.append(ass_obj)
            display(HTML("<hr>"))

    def assign_line(self, name, index=None, frequency=None, **kwargs):
        """ Mark a transition as assigned, and dump it into
            the assignments list attribute.

            The two methods for doing this is to supply either:
                1. U-line index
                2. U-line frequency
            One way or the other, the U-line Assignment object
            will be updated to signify the new assignment.
            Optional kwargs will also be passed to the Assignment
            object to update any other details.

            parameters:
            -----------------
            name - str denoting the name of the molecule
            index - optional arg specifying U-line index
            frequency - optional float specifying frequency to assign
            **kwargs - passed to update Assignment object
        """
        if index == frequency:
            raise Exception("Index/Frequency not specified!")
        ass_obj = None
        if index:
            # If an index is supplied, pull up from uline list
            ass_obj = self.ulines[index]
        elif frequency:
            for index, obj in enumerate(self.ulines):
                deviation = np.abs(frequency - obj.frequency)
                # Check that the deviation is sufficiently small
                if deviation <= (frequency * 1e-4):
                    # Remove from uline list
                    self.ulines.pop(index)
                    ass_obj = obj
        if ass_obj:
            ass_obj.name = name
            ass_obj.assigned = True
            # Unpack anything else
            ass_obj.__dict__.update(**kwargs)
            self.assignments.append(ass_obj)
        else:
            raise Exception("Peak not found! Try providing an index.")

    def get_assigned_names(self):
        """ Method for getting all the unique molecules out
            of the assignments, and tally up the counts.
        """
        names = [ass_obj.name for ass_obj in self.assignments]
        # Get unique names
        seen = set()
        seen_add = seen.add
        self.names = [name for name in names if not (name in seen or seen_add(name))]
        # Tally up the molecules
        self.identifications = {
            name: names.count(name) for name in self.names
            }
        return self.identifications

    def finalize_assignments(self):
        """
            Function that will complete the assignment process by
            serializing DataClass objects and formatting a report.
        """
        for ass_obj in self.assignments:
            # Dump all the assignments into YAML format
            ass_obj.to_file(
                "assignment_objs/{}-{}".format(ass_obj.experiment,ass_obj.peak_id),
                "yaml"
                )
        # Convert all of the assignment data into a CSV file
        ass_df = pd.DataFrame(
            data=[ass_obj.__dict__ for ass_obj in self.assignments]
            )
        ass_df.to_csv("reports/{0}.csv".format(self.session.experiment), index=False)
        self.table = ass_df
        tally = self.get_assigned_names()
        combined_dict = {
            "assigned_lines": len(self.assignments),
            "ulines": len(self.ulines),
            "peaks": self.peaks[self.freq_col].values,
            "num_peaks": len(self.peaks[self.freq_col]),
            "tally": tally,
            "unique_molecules": self.names,
            "num_unique": len(self.names)
            }
        # Combine Session information
        combined_dict.update(self.session.__dict__)
        # Dump to disk
        routines.dump_yaml(
            "sessions/{0}.yml".format(self.session.experiment),
            "yaml"
            )
        # Dump data to notebook output
        for key, value in combined_dict.items():
            print(key + ":   " + str(value))

    def clean_folder(self, action=False):
        """ Method for cleaning up all of the directories used by this routine.
            Use with caution!!!

            Requires passing a True statement to actually clean up.
        """
        folders = ["assignment_objs", "queries", "sessions", "clean", "reports"]
        if action is True:
            for folder in folders:
                rmtree(folder)


    def plot_assigned(self):
        """
            Generates a Plotly figure with the assignments overlaid
            on the experimental spectrum.
        """
        fig = go.FigureWidget()

        fig = go.FigureWidget()

        fig.add_scatter(
            x=assignmentsession.data["Frequency"],
            y=assignmentsession.data["Intensity"],
            name="Experiment",
            opacity=0.4
        )

        fig.add_bar(
            x=assignmentsession.table["frequency"],
            y=assignmentsession.table["intensity"],
            hoverinfo="text",
            text=assignmentsession.table["formula"] + "-" + assignmentsession.table["r_qnos"],
            name="Assignments"
        )
        # Store as attribute
        self.plot = fig

        return fig
