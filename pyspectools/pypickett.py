
import os
from glob import glob
from dataclasses import dataclass, field
import pickle
import shutil
import tempfile
from copy import deepcopy
from typing import List
from itertools import product

import numpy as np
import pandas as pd
import stat
from matplotlib import pyplot as plt
import pprint
import joblib
from tqdm.autonotebook import tqdm

from pyspectools import routines
from pyspectools.spcat import *
from pyspectools.parsefit import *
from pyspectools import parsers

class MoleculeFit:
    """ Class for handling the top level of a Pickett simulation.
        Inspired by PGopher, the `molecule` class stores information about
        whether we're dealing with a diatomic or polyatomic, number of lines
        to fit, temperature, etc.

        All of the information is bundled into a dictionary called `properties`.

        There are two methods right now that will generate a `molecule` object;
        `from_json` and `from_yaml`, which will interpret JSON and YAML input
        files respectively.
    """

    @classmethod
    def from_json(cls, json_filepath):
        # Generate a molecule from a specified JSON file
        json_data = read_json(json_filepath)
        species = MoleculeFit(json_data)
        return species

    @classmethod
    def from_yaml(cls, yaml_filepath):
        # Generate a molecule object from a YAML file
        yaml_data = read_yaml(yaml_filepath)
        species = MoleculeFit(yaml_data)
        return species

    @classmethod
    def from_pickle(cls, picklepath):
        # Generate a molecule object from a pickle instance
        with open(picklepath) as pickle_read:
            species = pickle.load(pickle_read)
        return species

    def __init__(self, options=None):
        # Initialize the default properties of a diatomic molecule
        self.properties = {
            "name": "Molecule",
            "tag": 0,
            "parameters": dict(),            # human input of parameters
            "linear": False,
            "symmetric": False,
            "prolate": False,
            "dipole": {
                "A": 0.,
                "B": 0.,
                "C": 0.,
            },
            "reduction": "A",
            "spin": 1,
            "spin degeneracy": 0,
        # These are options for the simulation
            "units": "MHz",
            "temperature": 5.,
            "intensity threshold": [-8., -3.],
            "K range": [0, 0],               # range for K quantum number
            "partition function": 1E3,
            "interactions": 0,
            "quantum number range": [0, 99],
            "odd state weight": 1,
            "even state weight": 1,
            "frequency limit": 100.,
            "vibration limit": 1,
            "vsym": 1,
            "ewt": 0,
            "statistical axis": 1,
            "number of parameters": 100,
            "number of lines": 0,
            "number of iterations": 10,
            "number of skipped lines": 0,
            "marquadrt-levenburg": 0.0000E+00,
            "maximum error": 1E+13,
            "fractional importance": 1.0000E+00,
            "IR scaling": 1.0,
            "diagonalization": 0,
            "xopt": 1
        }

        self.param_objects = dict()      # stores parameter objects

        self.iteration_count = 1         # running count of iterations
        self.iterations = dict()         # stores data from each iteration
        self.top_dir = os.getcwd()       # top directory always stored
        self.cwd = ""                    # current working directory

        self.experimental_lines = dict() # experimental lines that go to a .lin

        # Function that updates parameters, and re-writes the .par and .int
        # files.
        self.initialize(options)
        self.interactive = False              # Interactive plotting option

    def initialize(self, options=None):
        """ Method that will rewrite the .par and .int files, after updating
            the properties dictionary.

            Any warnings regarding common mistakes in input files should also be
            flagged here.
        """
        if options is not None:
            self.properties.update(options)

        self.generate_parameter_objects(verbose=False)
        self.check_input()

        self.setup_int()
        self.setup_par()

        print("Initialized settings for molecule " + self.properties["name"])

    def check_input(self):
        """ Method for going through the input settings and flagging common
            mistakes.
        """
        # If the K range is set to 0, but the molecule is not linear
        if self.properties["K range"][1] == 0 and self.properties["linear"] is False:
            raise UserWarning("Warning: You have specified a non-linear molecule \
                               and the maximum K value is unset. Result cat file \
                               may be empty!")

        # If A and C constants are given and molecule is supposed to be linear
        if ["A", "C"] in list(self.properties["parameters"].keys()):
            if self.properties["linear"] is True:
                raise UserWarning("Molecule flagged as linear, \
                                   but A and C constants specified.")

    def generate_parameter_objects(self, verbose=True):
        for param_key in self.properties["parameters"]:
            self.param_objects[param_key] = self.parameter(
                param_key,
                self.properties["parameters"][param_key],
                self.properties["reduction"],
                linear=self.properties["linear"],
                verbose=verbose
            )

    def nuclear(self, delete=False):
        """ Function that cleans up Pickett files in a folder """
        filelist = [".cat", ".var", ".par", ".int", "_parsedlines.csv",
                    "_spectrum.pdf", ".fit", ".out"]
        files = [self.properties["name"] + name for name in filelist]
        if delete is True:
            for file in files:
                try:
                    os.system("rm " + file)
                except FileNotFoundError:
                    # Ignore files that aren't in the folder
                    pass
        else:
            raise EnvironmentError("Please provide a True value to confirm deletion!")

    def toggle_interactive(self, connected=False):
        """ Method to toggle interactive plots on and off """
        self.interactive = not self.interactive
        init_notebook_mode(connected=connected)
        if self.interactive is False:
            # Pseudo-disconnect interactivity by removing JS injections
            init_notebook_mode(connected=True)

    def setup_par(self):
        """ Function that provides the correct formatting for a .par file """
        opt_line = ""
        opt_line += str(self.properties["number of parameters"]).rjust(4) + " "
        opt_line += str(self.properties["number of lines"]).rjust(5) + " "
        opt_line += str(self.properties["number of iterations"]).rjust(5) + " "
        opt_line += str(self.properties["number of skipped lines"]).rjust(5) + " "
        opt_line += str(self.properties["marquadrt-levenburg"]).rjust(12) + " "
        opt_line += str(self.properties["maximum error"]).rjust(15) + " "
        opt_line += str(self.properties["fractional importance"]).rjust(15) + " "
        opt_line += str(self.properties["IR scaling"]).rjust(13) + " "

        prop_line = ""
        prop_line += str("'" + self.properties["reduction"] + "'").rjust(3)
        # Format the spin degeneracy sign - if it's positive, we use asym top
        # quantum numbers. If negative use symmetric top.
        if self.properties["symmetric"] is True and self.properties["linear"] is False:
            prop_line += str(np.negative(self.properties["spin degeneracy"])).rjust(3) + " "
        elif self.properties["symmetric"] is False and self.properties["linear"] is False:
            prop_line += str(np.absolute(self.properties["spin degeneracy"])).rjust(3) + " "
        elif self.properties["symmetric"] is False and self.properties["linear"] is True:
            prop_line += str(np.negative(self.properties["spin degeneracy"])).rjust(3) + " "
        # Format the sign of vibration limit; negative treats top as oblate case
        # while positive treats the prolate case
        if self.properties["prolate"] is True and self.properties["linear"] is False:
            prop_line += str(np.absolute(self.properties["vibration limit"])).rjust(3) + " "
        elif self.properties["prolate"] is False and self.properties["linear"] is False:
            prop_line += str(np.negative(self.properties["vibration limit"])).rjust(3) + " "
        else:
            prop_line += str(self.properties["vibration limit"]).rjust(3) + " "
        prop_line += str(self.properties["K range"][0]).rjust(3) + " "
        prop_line += str(self.properties["K range"][1]).rjust(3) + " "
        prop_line += str(self.properties["interactions"]).rjust(3) + " "
        prop_line += str(self.properties["statistical axis"]).rjust(3) + " "
        prop_line += str(self.properties["even state weight"]).rjust(3) + " "
        prop_line += str(self.properties["odd state weight"]).rjust(3) + " "
        prop_line += str(self.properties["vsym"]).rjust(3)
        prop_line += str(self.properties["ewt"]).rjust(3)
        prop_line += str(self.properties["diagonalization"]).rjust(3)
        prop_line += str(self.properties["xopt"]).rjust(3)
        # may be missing EWT

        with open(self.properties["name"] + ".par", "w+") as write_file:
            write_file.write(self.properties["name"] + "\n")
            write_file.write(opt_line + "\n")
            write_file.write(prop_line + "\n")
            for parameter in self.param_objects:
                par_line = self.param_objects[parameter].format_line()
                write_file.write(par_line + "\n")

    def setup_int(self):
        """ Setup the int file. Order of things written:
            1. units
            2. molecular tag identifier
            3. partition function
            4. quantum number lower limit
            5. quantum number upper limit
            6. intensity threshold lower limit
            7. intensity threshold upper limit
            8. frequency limit in GHz
            9. vibrational quantum number limit
            10. dipole moments
        """
        settings_line = " "                                # units of the sim
        if self.properties["units"] == "wavenumbers":
            settings_line += "1"
        elif self.properties["units"] == "MHz":
            settings_line += "0"
        else:
            settings_line += "0"
        settings_line += " " + str(self.properties["tag"]).rjust(6)
        settings_line += str(self.properties["partition function"]).rjust(10)
        for value in self.properties["quantum number range"]:
            settings_line += str(value).rjust(5)
        for value in self.properties["intensity threshold"]:
            settings_line += str(value).rjust(7)
        settings_line += str(self.properties["frequency limit"]).rjust(9)
        settings_line += str(self.properties["temperature"]).rjust(6)
        settings_line += str(self.properties["vibration limit"]).rjust(7)

        with open(self.properties["name"] + ".int", "w+") as write_file:
            write_file.write(self.properties["name"] + "\n")
            write_file.write(settings_line + "\n")
            for projection in self.properties["dipole"]:
                if projection == "A":
                    write_file.write(" 1      " + str(self.properties["dipole"][projection]) + "\n")
                elif projection == "B":
                    write_file.write(" 2      " + str(self.properties["dipole"][projection]) + "\n")
                elif projection == "C":
                    write_file.write(" 3      " + str(self.properties["dipole"][projection]) + "\n")

    def fit_lines(self, verbose=True):
        os.chdir(self.top_dir)
        if os.path.isfile(self.properties["name"] + ".lin") is False:
            runinput = input("Run calbak to generate a .lin file from .cat? (Y/N)")
            if runinput.lower() == "y":
                run_calbak(self.properties["name"])
            else:
                raise FileNotFoundError(self.properties["name"] + ".lin not found!")

        # Read in the .lin file to to figure out the number of lines we're going
        # to use in the fit. Right now, the contents are saved to a lin_file
        # attribute as a list, but can potentially be used to do more...
        with open(self.properties["name"] + ".lin") as read_file:
            self.lin_file = read_file.readlines()
            #self.properties["number of lines"] = len(self.lin_file)

        # Figure out what the next iteration is
        folder_number = generate_folder()
        self.cwd = str(folder_number) + "/"
        # If this is the first iteration, backup the data before we do anything
        if folder_number == 1:
            if os.path.isdir("initial") is False:
                os.mkdir("initial")
            backup_files(self.properties["name"], "./initial/")
        # Copy files to the work directory
        backup_files(self.properties["name"], self.cwd)

        # write the settings used for the current simulation to disk
        dump_yaml(str(folder_number) + "/" + self.properties["name"] + ".yml",
                  self.properties
                  )
        # change directory to the new working directory
        os.chdir(self.cwd)
        # Write the .int and .par file to disk
        self.setup_int()
        self.setup_par()
        # Run SPFIT to fit the lines
        run_spfit(self.properties["name"])
        # Create an instance of a fit object, and add it to the pile
        self.iterations[self.iteration_count] = fit_output(
                self.properties["name"] + ".fit",
                verbose=verbose,
                interactive=self.interactive
            )
        self.predict_lines()
        os.chdir(self.top_dir)

        # Update the parameters in the molecule instance!
        current_params = self.iterations[self.iteration_count].export_parameters()
        self.iteration_count += 1
        self.update_parameters(current_params, verbose=False)

        # Save the updated parameters to disk
        dump_yaml(self.cwd + self.properties["name"] + ".fit.yml", self.properties)

        print("Current parameters (MHz)")
        for parameter in current_params:
            print(parameter + "\t" + str(current_params[parameter]["value"]) + \
                  "(" + str(current_params[parameter]["uncertainty"] + ")"))

    def calbak(self):
        """ Run calbak to generate a .lin file from .cat """
        run_calbak(self.properties["name"])
        with open(self.properties["name"] + ".lin") as read_file:
            print(read_file.read())

    def predict_lines(self, verbose=True):
        """ Run SPCAT to predict lines based on a .var and .int file.
            This will operate in the current directory - since it does not
            need to overwrite any files, we don't actually back up anything!

            The predicted lines are also parsed into a .csv file, and plot up
            into: (A) a notebook inline plot, and (B) a PDF output.

            SPCAT is run twice - first to get the partition function, and second
            to get the correct intensities.
        """
        # First pass of SPCAT; get the partition function
        self.properties["partition function"] = run_spcat(
            self.properties["name"],
            temperature="{:.3f}".format(self.properties["temperature"])
            )
        # Make sure the int file has the correct partition function
        self.setup_int()
        # Second pass of SPCAT with correct intensities
        run_spcat(self.properties["name"])
        # Parse the output of SPCAT
        self.cat_lines = parsers.parse_cat(
            self.properties["name"] + ".cat",
        )
        print("Saving the parsed lines to " + self.properties["name"] + "_parsedlines.csv")
        self.cat_lines.to_csv(self.properties["name"] + "_parsedlines.csv")

        # Plot the .cat file up
        fig, ax = plot_pickett(self.cat_lines, verbose=verbose)
        os.chdir(self.top_dir)
        fig.savefig(self.properties["name"] + "_spectrum.pdf", format="pdf")

    def copy_settings(self, iteration=0):
        """ Copy settings used in a previous iteration
            If none specified, we'll take the settings from before the first fit
        """
        if iteration == 0:
            iteration = "initial"
        #current_params = self.iterations[iteration].export_parameters()
        iteration_folder = str(iteration) + "/" + self.properties["name"]
        if os.path.isfile(iteration_folder + ".fit.yml") is True:
            iteration_file = iteration_folder + ".fit.yml"
        else:
            iteration_file = iteration_folder + ".yml"
        iteration_params = read_yaml(iteration_file)
        self.properties.update(iteration_params)
        print("Settings copied from " + iteration_file)

    def update_parameters(self, parameters, verbose=True):
        """ Update the simulation parameters
            This is written with a loop in order to make sure the
            keys that aren't specified remain the same as before without
            change.
        """
        for key in parameters:
            self.properties["parameters"][key].update(parameters[key])
        self.generate_parameter_objects(verbose=verbose)

    def backup(self, comment=None):
        """ Method for backing up files for a specific reason.
            The most common use I have for this is to backup .cat and .lin files
            for whatever reason (adding/removing lines to/from fit)
        """
        if comment is None:
            raise RuntimeError("Please provide a comment for this backup!")
        if os.path.isdir("backup") is False:
            os.mkdir("backup")
        os.chdir("backup")
        folder_number = generate_folder()
        with open(str(folder_number) + "/README", "w+") as write_file:
            write_file.write(comment)
        os.chdir(self.top_dir)
        backup_files(self.properties["name"], "./backup/" + str(folder_number))

    def restore_backup(self, number):
        """ Restores backed up files from the `backup` method """
        files = glob("backup/" + str(number) + "/*")
        for file in files:
            shutil.copy2(file, self.top_dir)
        print("Restored from backup " + str(number))

    def save_progress(self):
        if os.path.isdir("instances") is False:
            os.mkdir("instances")
        counter = 0
        while os.path.exists("instances/" + self.properties["name"] + ".%s.pickle" % counter):
            counter += 1
        with open("instances/" + self.properties["name"] + "." + str(counter) + ".pickle", "wb") as write_file:
            pickle.dump(self, write_file, pickle.HIGHEST_PROTOCOL)

    def set_fit(self, fit=True, params=None, verbose=False):
        """ Function to flip all parameters to fit or not to fit.
            The default behaviour, if nothing is provided, is to fit all the
            parameters.

            A list of parameters can be supplied to the params argument, which
            will specifically fit or not fit those parameters.
        """
        if params is None:
            params = list(self.properties["parameters"].keys())
        if type(params) is str:
            if params not in list(self.properties["parameters"].keys()):
                raise KeyError("Parameter " + parameter + " is not in your parameter list!")
            else:
                self.properties["parameters"][parameter]["fit"] = fit
        elif type(params) is list:
            for parameter in params:
                if parameter not in list(self.properties["parameters"].keys()):
                    raise KeyError("Parameter " + parameter + " is not in your parameter list!")
                else:
                    self.properties["parameters"][parameter]["fit"] = fit
        self.generate_parameter_objects(verbose=verbose)

    def report_parameters(self):
        """ Method to return a dictionary of only the parameter values """
        param_dict = dict()
        for parameter in self.properties["parameters"]:
            param_dict[parameter] = self.properties["parameters"][parameter]["value"]
        return param_dict

    def finalize(self, iteration=None):
        """ Been hard to keep track which iteration was the final one
            sometimes, and so this function will "finalize" the fits by
            creating a separate folder for the final fits.
        """
        if iteration is None:
            # If no iteration is specified, the last iteration is used
            iteration = self.iteration_count - 1
            print("No iteration specified, using the last iteration.")
        if os.path.isdir(self.top_dir + "/final") is True:
            confirmation = input("Final folder exists. Confirm deletion? Y/N").lower()
            if confirmation == "y":
                shutil.rmtree(self.top_dir + "/final")
            else:
                raise ValueError("Final folder exists, and not deleting.")
        # Copy the files over from designated iteration
        shutil.copytree(
            self.top_dir + "/" + str(iteration),
            self.top_dir + "/final",
            )
        # Generate a report for the final fits
        for parameter in self.properties["parameters"]:
            if "uncertainty" in list(self.properties["parameters"][parameter].keys()):
                self.properties["parameters"][parameter]["formatted"] = str(self.properties["parameters"][parameter]["value"]) + \
                                         "(" + str(self.properties["parameters"][parameter]["uncertainty"]) + \
                                         ")"
            else:
                self.properties["parameters"][parameter]["formatted"] = str(self.properties["parameters"][parameter]["value"])
        report_df = pd.DataFrame.from_dict(self.properties["parameters"]).T
        with open(self.top_dir + "/final/parameter_report.html", "w+") as write_file:
            write_file.write(report_df.to_html())
        for file in glob(self.top_dir + "/final/*"):
            # Make the final files read-only
            os.chmod(file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


    def parameter_scan(self, parameter, values, relaxed=False):
        """ A method for automatically scanning parameter space to see
            what values give a minimum RMS.

            We will repeatedly call SPFIT to estimate the fitting error
            based on what the parameters are. There are two modes depending
            on the `relaxed` variable: whether or not all other values are
            allowed to relax (like a relaxed potential energy scan) or if
            all parameters are held frozen.

            Args:
            parameter - a string input name of the parameter to change
            values - a list or array of values to be scanned
            relaxed - boolean determining whether the parameters are fixed
        """
        # Make sure none of the parameters are being fit
        self.set_fit(fit=relaxed, verbose=False)
        # We won't fit the scanning parameter regardless of relaxation
        self.properties["parameters"][parameter]["fit"] = False
        # "Remember" what the parameter settings were at this point in time
        parameter_dict = self.properties
        # Loop over all the specified values
        parameter_values = list()
        for index, value in enumerate(values):
            # Reset the parameters to scan values
            self.properties.update(parameter_dict)
            self.properties["parameters"][parameter]["value"] = value
            self.generate_parameter_objects(verbose=False)
            # Silence the output
            self.fit_lines(verbose=False)
            # Record the final RMS error for this parameter
            current_params = self.report_parameters()
            last_key = max(list(self.iterations.keys()))
            current_params["rms"] = self.iterations[last_key].fit_properties["final rms"]
            parameter_values.append(current_params)
        scan_report = pd.DataFrame(parameter_values)

        fig, ax = plt.subplots(figsize=(14,5.5))

        ax.scatter(
            x=scan_report[parameter].values,
            y=scan_report["rms"].values,
        )
        ax.set_ylabel("RMS (MHz)")
        ax.set_xlabel(parameter + " value (MHz)")
        ax.set_xticks(values)
        ax.set_xticklabels(values)
        ax.set_title("Parameter scan for " + parameter)

        fig.savefig("scan_" + parameter + "_" + str(values[0]) + "-" + str(values[-1]) + ".pdf",
                    format="pdf"
                )
        if isnotebook() is True:
            plt.show()
        else:
            pass

        self.scan_report = scan_report

    class parameter:
        """ Class for handling parameters in Pickett.
            Each instance has a human name (i.e. A, B, C constants) which
            is to be translated to Pickett identifiers.

            A `fit` flag is used to denote whether or not the parameter
            is going to be fit, which automatically sets the uncertainty
            to a large number.

            Nuclear spin terms will have the nuclei identifier
        """
        def __init__(self, name, parameter_dict, reduction="A", linear=True, verbose=True):
            # Initialize values for parameters
            self.variables = {
                "name": name,
                "value": 0.,
                "uncertainty": 1E-25,
                "fit": False,
                "identifier": 0,
                "nuclei": 0,
            }
            # After initializing, put in user values
            self.variables.update(parameter_dict)
            self.fit_check()
            if verbose is True:
                # Format the parameter printing to be nicer
                pp = pprint.PrettyPrinter(indent=4)
                pp.pprint(self.variables)
            if self.variables["nuclei"] == 0 \
            and self.variables["name"] in ["eQq", "eQq/2"]:
                # warning message issued if no nucleus specified
                print("You have specificed a hyperfine parameter, but")
                print("did not specify a nucleus in your input file.")
                raise ValueError("Hyperfine parameter with no nuclei ID!")

            # Convert the human parameter name to a Pickett identifier
            self.variables["identifier"] = human2pickett(self.variables["name"],
                                                         reduction,
                                                         linear,
                                                         self.variables["nuclei"] + 1)
            if self.variables["fit"] is True:
                # flag variable for fitting
                self.variables["uncertainty"] = 1E+25

        def format_line(self):
            """ Formats a string output to match the Pickett format for a parameter """
            pickett_line = str(self.variables["identifier"]).rjust(13)
            pickett_line += str(self.variables["value"]).rjust(24)
            pickett_line += str(self.variables["uncertainty"]).rjust(16)
            pickett_line += " /" + self.variables["name"]
            return pickett_line

        def fit_check(self):
            """ A function to flip the uncertainty if we will fit a parameter """
            if self.variables["fit"] is True:
                self.variables["uncertainty"] = 1E+25
            else:
                self.variables["uncertainty"] = 1E-25

    class exp_line:
        """ Class for an experimental line. Converts to and from .lin files. """
        def __init__(self, frequency, uncertainty, lower_num, upper_num, comment=None):
            self.properties = {
                "frequency": frequency,               # frequency in MHz
                "uncertainty": uncertainty,           # uncertainty in MHz
                "lower quantum nos.": lower_num,      # list of quantum numbers
                "upper quantum nos.": upper_num,      # for lower and upper states
                "comment": comment
            }

        def update_line(self, line_dict):
            self.properties.update(line_dict)
            print(self.properties)

        def format_quantum_numbers(quantum_list):
            line = ""
            for value in quantum_list:
                line += str(value).rjust(3)       # each quantum number is right
            return line                           # justified by 3 positions...

        def format_line(self):
            # Formatting for a .lin line
            line = ""
            line += self.format_quantum_numbers(self.properties["upper quantum nos."])
            line += self.format_quantum_numbers(self.properties["lower quantum nos."])
            line += str(self.properties["frequency"]).rjust(33)
            line += str(self.properties["uncertainty"]).rjust(9)
            if self.properties["comment"] is not None:
                line += " /" + self.properties["comment"]
            return line


@dataclass
class QuantumNumber:
    """
    Class for a Quantum Number.
    """
    value: int
    upper: bool = False
    max: int = 10
    min: int = 0

    def __post_init__(self):
        if self.min is None:
            self.min = 0
        if self.max is None:
            self.max = 10

    def __repr__(self):
        return str(self.value)

    def __str__(self):
        return str(self.value)

    def roll(self, seed=None):
        """
        Generate a random value for the current quantum number.
        """
        np.random.seed(seed)
        self.value = np.random.randint(
            self.min,
            self.max,
            1
        )[0]

    def spawn_upper(self):
        """
        Create a new QuantumNumber instance for the corresponding upper state.
        Will also shift the quantum number to a new value, +/-2,1,0.

        Returns
        -------
        QuantumNumber: object
            A deep copy of the current QuantumNumber instance, but also shifts the
            quantum number by a random integer.
        """
        np.random.seed()
        delta = np.random.randint(-3, 3, 1)[0]
        new_qno = deepcopy(self)
        new_qno.value += delta
        # Can't have negative quantum numbers
        if new_qno.value < 0:
            new_qno.value = 0
        new_qno.upper = True
        return new_qno

@dataclass
class Transition:
    frequency: float
    n_numbers: int
    quantum_numbers: List = field(default_factory=list)
    lower_state: List = field(default_factory=list)
    upper_state: List = field(default_factory=list)
    max_values: List = field(default_factory=list)
    min_values: List = field(default_factory=list)
    uncertainty: float = 0.005

    def __post_init__(self):
        if self.max_values is None:
            self.max_values = [10 for i in range(self.n_numbers)]
        if self.min_values is None:
            self.min_values = [0 for i in range(self.n_numbers)]
        if self.uncertainty is None:
            self.uncertainty = 0.005
        # Initialize the quantum numbers
        self.lower_state = [
            QuantumNumber(0, min=minval, max=maxval) for minval, maxval in zip(self.min_values, self.max_values)
        ]

    def random_quantum_numbers(self):
        """
        Create a random set of quantum numbers for a transition. First, the lower state is generated based on
        lower and upper bounds specified by the user. A deepcopy is made for each lower state quantum number,
        and the associated upper state number is created by shifting the lower value by a random integer between
        -2 and 2.
        Returns
        -------

        """
        for qno in self.lower_state:
            qno.roll()
        self.upper_state = [qno.spawn_upper() for qno in self.lower_state]
        self.quantum_numbers = [
            [str(qno) for qno in self.lower_state],
            [str(qno) for qno in self.upper_state]
        ]
        return self.quantum_numbers

    def __str__(self):
        """
        Method to format the quantum numbers into lin file format.

        Returns
        -------
        line: str
            Upper and lower state quantum numbers formatted into lin
            format.
        """
        line = "  {upper} {lower}                 {frequency}     {uncertainty}"
        format_dict = {
            "upper": "  ".join(self.quantum_numbers[1]),
            "lower": "  ".join(self.quantum_numbers[0]),
            "frequency": self.frequency,
            "uncertainty": self.uncertainty
        }
        return line.format(**format_dict)


@dataclass
class AutoFitSession:
    filename: str
    n_numbers: int
    frequencies: List = field(default_factory=list)
    uncertainties: List = field(default_factory=list)
    max_values: List = field(default_factory=list)
    min_values: List = field(default_factory=list)
    method: str = "mc"
    rms_target: float = 1.
    niter: int = 10000
    nprocesses: int = 1
    verbose: int = 0

    @classmethod
    def from_yml(cls, filepath):
        """
        Class method to read the settings for an AutoFitSession via YAML file.
        Parameters
        ----------
        filepath: str
            Filepath to the YAML settings file

        Returns
        -------
        AutoFitSession object
        """
        session = cls(**routines.read_yaml(filepath))
        return session

    @classmethod
    def from_pkl(cls, filepath):
        """
        Function to load a previously saved Pickle instance of AutoFitSession.

        Parameters
        ----------
        filepath: str
            Filepath to the pickle file to load.

        Returns
        -------
        Loaded
        """
        cls = routines.read_obj(filepath)
        if cls.__class__.name != "AutoFitSession":
            raise Exception("Target file is not an AutoFitSession object!")
        else:
            return cls

    def __post_init__(self):
        if self.uncertainties is None:
            self.uncertainties = [0.005 for i, _ in enumerate(self.frequencies)]
        if os.path.exists(self.filename + ".par") is False:
            raise Exception(".par file does not exist!")
        with open(self.filename + ".par") as read_file:
            self.par = read_file.readlines()
        self.wd = os.getcwd()
        # Setup filestructure
        for folder in ["fits", "yml"]:
            if os.path.exists(folder) is False:
                os.mkdir(folder)
        if self.method not in ["mc", "brute"]:
            raise Exception("Testing method not implemented!")
        else:
            if self.method == "mc":
                self._iteration = self._rng

    def _brute_generator(self):
        """
        Create a generator that will brute force every possible combination of quantum numbers.
        The user sets the maximum values for each quantum number, and this function will produce a
        generator.

        Returns
        -------
        product
            Product generator from itertools
        """
        # Generate a list of lists that correspond to possible maximum values
        values = [list(range(val + 1)) for val in self.max_values * 2]
        return product(*values)

    def _rng(self, index):
        """
        Private method to perform a single iteration of the whole process. Starts by generating random quantum
        numbers, and creates a temporary folder to run SPFIT along with the .par and .lin files.

        The output .fit file is parsed, and both the parsed data and the .fit file is copied back over to
        the working directory. An index argument will let the function be run in async while still letting
        the specific file index be known.

        Parameters
        ----------
        index: int
            Index that keeps track of the file name to save the output as

        Returns
        -------
        index, rms: 2-tuple
            The current iteration index and the RMS for this iteration
        """
        pkg = zip(
            self.frequencies,
            self.uncertainties,
        )
        # Initialize the Transition object, which handles all of the quantum numbers for a given transition
        transitions = [
            Transition(
                frequency,
                uncertainty=uncertainty,
                n_numbers=self.n_numbers,
                max_values=self.max_values,
                min_values=self.min_values
            ) for frequency, uncertainty in pkg
        ]
        # Generate the quantum numbers for each transition
        _ = [transition.random_quantum_numbers() for transition in transitions]
        # Make up the lin file
        lines = "\n".join([str(transition) for transition in transitions])
        # Setup a temporary directory to run SPFIT in
        with tempfile.TemporaryDirectory() as path:
            os.chdir(path)
            with open(self.filename + ".lin", "w+") as write_file:
                write_file.write(lines)
            with open(self.filename + ".par", "w+") as write_file:
                write_file.writelines(self.par)
            # Run SPFIT
            routines.run_spfit(self.filename)
            shutil.copy2(self.filename + ".fit", os.path.join(self.wd, "fits/{}.fit".format(index)))
            # Parse the output
            fit_dict = parsers.parse_fit(self.filename + ".fit")
            # Copy some of the data back over
            routines.dump_yaml(os.path.join(self.wd, "yml/{}.yml".format(index)), fit_dict)
            # Not sure if this is necessary, but just in case
            os.chdir(self.wd)
        return index, fit_dict["rms"]

    def run(self, niter=None, nprocesses=None, headless=True):
        """
        Run the search for assignments. The function wraps a private method, and is parallelized with a joblib
        Pool.
        Parameters
        ----------
        niter: int or None, optional
            Number of iterations. If None, uses the class attribute, otherwise the user specified value.
        nprocesses: int or None, optional
            Number of parallel jobs to break the task into. If None, uses the class attribute.

        Returns
        -------
        results: list
            List of the final RMS values from individual SPFIT runs.
        """
        if niter is None:
            niter = self.niter
        if nprocesses is None:
            nprocesses = self.nprocesses
        pool = joblib.Parallel(
            n_jobs=nprocesses,
            verbose=self.verbose,
        )
        iterator = range(niter)
        if headless is False:
            iterator = tqdm(iterator)
        # Distribute and run the quantum number testing
        results = pool(
            joblib.delayed(self._iteration)(i) for i in iterator
        )
        self.results = results
        return results

    def save(self, filepath=None):
        """
        Dump the current session to disk as a Pickle file.

        Parameters
        ----------
        filepath: str, optional
            Filepath to save the file to. If None, uses the filename attribute plus .pkl extension
        """
        if filepath is None:
            filepath = self.filename + ".pkl"
        routines.save_obj(self, filepath)

