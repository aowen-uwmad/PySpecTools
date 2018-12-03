
from astroquery.splatalogue import Splatalogue
from astropy import units as u
from lmfit import models
import numpy as np
import pandas as pd
import peakutils
from scipy.signal import savgol_filter
from . import plotting
from pyspectools import fitting
from itertools import combinations, chain


def fit_line_profile(spec_df, frequency, intensity=None, name=None, verbose=False):
    """ Low level function wrapper to to fit line profiles
        in chirp spectra.
    """
    if "Cleaned" not in spec_df.columns:
        spec_df["Cleaned"] = spec_df["Intensity"].values
    model = models.VoigtModel()
    params = model.make_params()
    # Set up boundary conditions for the fit
    params["center"].set(
        frequency,
        min=frequency - 0.03,
        max=frequency + 0.03
    )
    # If an intensity is supplied
    if intensity:
        params["amplitude"].set(intensity)
    params["sigma"].set(
        0.05,
        min=0.04,
        max=0.07
    )
    freq_range = [frequency + offset for offset in [-5., 5.]]
    slice_df = spec_df[
        (spec_df["Frequency"] >= freq_range[0]) & (spec_df["Frequency"] <= freq_range[1])
    ]
    # Fit the peak lineshape
    fit_results = model.fit(
        slice_df["Intensity"],
        params,
        x=slice_df["Frequency"],
    )
    if verbose is True:
        print(fit_results.fit_report())
    yfit = fit_results.eval(x=spec_df["Frequency"])
    if name:
        spec_df[name] = yfit
    # Subtract the peak contribution
    spec_df["Cleaned"] -= yfit
    return fit_results


def peak_find(spec_df, col="Intensity", thres=0.015):
    """ Wrapper for peakutils applied to pandas dataframes """
    peak_indices = peakutils.indexes(
        spec_df[col],
        thres=thres
        )
    return spec_df.iloc[peak_indices]


def search_center_frequency(frequency, width=0.5):
    """ Wrapper for the astroquery Splatalogue search
        This function will take a center frequency, and query splatalogue
        within the CDMS and JPL linelists for carriers of the line.

        Input arguments:
        frequency - float specifying the center frequency
    """
    min_freq = frequency - width
    max_freq = frequency + width
    splat_df = Splatalogue.query_lines(
        min_freq*u.MHz,
        max_freq*u.MHz,
        line_lists=["CDMS", "JPL"]
    ).to_pandas()
    # These are the columns wanted
    columns = [
        "Species",
        "Chemical Name",
        "Meas Freq-GHz(rest frame,redshifted)",
        "Freq-GHz(rest frame,redshifted)",
        "Resolved QNs",
        "CDMS/JPL Intensity",
        "E_U (K)"
        ]
    # Take only what we want
    splat_df = splat_df[columns]
    splat_df.columns = [
        "Species",
        "Chemical Name",
        "Meas Freq-GHz",
        "Freq-GHz",
        "Resolved QNs",
        "CDMS/JPL Intensity",
        "E_U (K)"
        ]
    # Now we combine the frequency measurements
    splat_df["Combined"] = splat_df["Meas Freq-GHz"].values
    # Replace missing experimental data with calculated
    splat_df["Combined"].fillna(splat_df["Freq-GHz"], inplace=True)
    # Convert to MHz
    splat_df["Combined"] *= 1000.
    return splat_df


def assign_peaks(spec_df, frequencies, **kwargs):
    """ Higher level function that will help assign features
        in a chirp spectrum.

        Input arguments:
        spec_df - pandas dataframe containing chirp data
        frequencies - iterable containing center frequencies of peaks
        Optional arguments are passed into the peak detection as well
        as in the plotting functions.

        Returns a dataframe containing all the assignments, and a list
        of unidentified peak frequencies.
    """
    unassigned = list()
    dataframes = pd.DataFrame()
    for frequency in frequencies:
        splat_df = search_center_frequency(frequency)
        nitems = len(splat_df)
        # Only act if there's something found
        if nitems > 0:
            splat_df["Deviation"] = np.abs(
                    splat_df["Combined"] - frequency
                    )
            print(splat_df)
            try:
                print("Peak frequency is " + str(frequency))
                index = int(
                    input(
                        "Please choose an assignment index: 0-" + str(nitems - 1)
                        )
                )
                assigned = True
            except ValueError:
                print("Deferring assignment")
                unassigned.append(frequency)
                assigned = False
            # Take the assignment. Double brackets because otherwise
            # a series is returned rather than a dataframe
            if assigned is True:
                assignment = splat_df.iloc[[index]].sort_values(
                    ["Deviation"],
                    ascending=False
                )
                ass_freq = assignment["Combined"]
                # If the measurement is not available, go for
                # the predicted value
                ass_name = assignment["Species"] + "-" + assignment["Resolved QNs"]
                # Clean the line
                _ = fit_line_profile(spec_df, frequency)
                # Keep track of the assignments in a dataframe
                dataframes = dataframes.append(assignment)
            print("----------------------------------------------------")
        else:
            print("No species known for " + str(frequency))
    return dataframes, unassigned


def harmonic_search(frequencies, maxJ=10, dev_thres=5., prefilter=False):
    """
        Function that will search for possible harmonic candidates
        in a list of frequencies. Wraps the lower level function.

        Generates every possible 4 membered combination of the
        frequencies, and makes a first pass filtering out unreasonable
        combinations.

        parameters:
        ----------------
        frequencies - iterable containing floats of frequencies (ulines)
        maxJ - maximum value of J considered for quantum numbers
        dev_thres - standard deviation threshold for filtering unlikely
                    combinations of frequencies
    """
    frequencies = np.sort(frequencies)
    # Generate all possible three membered combinations
    combo_gen = combinations(frequencies, 4)

    candidates = list()
    
    # Sweep through all possible combinations, and look
    # for viable candidates
    if prefilter is True:
        for length in [3, 4]:
            for combo in combinations(frequencies, length):
                stdev = np.std(np.diff(combo))
                if np.std(np.diff(combo)) <= dev_thres:
                    candidates.append(combo)
    elif prefilter is False:
        candidates = chain(
            combinations(frequencies, 3),
            combinations(frequencies, 4)
        )

    data_list = list()
    fit_results = list()

    # Perform the fitting procedure on candidate combinations
    for index, candidate in enumerate(candidates):
        min_rms, min_index, _, fit_values, fit_objs = fitting.harmonic_fit(
            candidate, 
            maxJ=maxJ, 
            verbose=False
            )
        data_list.append(
            [min_index, min_rms, list(fit_values.values())]
            )
        fit_results.append(fit_objs)

    results_df = pd.DataFrame(
        data=data_list,
        names=["Index", "RMS", "B", "D"]
        )

    results_df.sort_values(["RMS"], ascending=True, inplace=True)

    return results_df, fit_results
