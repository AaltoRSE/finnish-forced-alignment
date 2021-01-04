#!/usr/bin/env python
# coding: utf-8

# Imports
import pandas as pd
from wer import wer  # TODO Does this work outside pycharm?
import numpy as np
import matplotlib.pyplot as plt
import argparse
from scipy.stats import percentileofscore


def parse_arguments():
    parser = argparse.ArgumentParser(description='Calculate metrics from two ctms, first one gold standard')
    parser.add_argument('gold', type=str,
                    help='The gold standard ctm, which to compare to')
    parser.add_argument('created', type=str,
                    help='The created ctm, which are evaluated')
    parser.add_argument('name', type=str,
                    help='Root name for the generated files')
    args = parser.parse_args()
    return args


# Read the ctms to a dataframe
def create_ctm_dfs(gold_ctms_file, created_ctms_file):
    column_names = ["Filename", "segment", "start", "duration", "token"]

    gold_ctms_df = pd.read_csv(gold_ctms_file, sep=' ', names=column_names, index_col=False, engine='python')
    created_ctms_df = pd.read_csv(created_ctms_file, sep=' ', names=column_names, index_col=False, engine='python')

    # Create end-time column
    '''df['c'] = df.apply(lambda row: row.a + row.b, axis=1)
    If you get the SettingWithCopyWarning you can do it this way also:

    fn = lambda row: row.a + row.b # define a function for the new column
    col = df.apply(fn, axis=1) # get column data with an index
    df = df.assign(c=col.values) # assign values to column 'c'
    '''
    gold_ctms_df["end"] = gold_ctms_df.apply(lambda row: row.start + row.duration, axis=1)
    created_ctms_df["end"] = created_ctms_df.apply(lambda row: row.start + row.duration, axis=1)

    return gold_ctms_df, created_ctms_df



# Initialize frame counts and get list of files.
# Iterate file by file
list_of_filenames = gold_ctms_df["Filename"].unique().tolist()
number_of_files = len(list_of_filenames)

# Faster method of doing frame-by-frame comparison
fast_total_correct_empty_frames = []
fast_total_correct_token_frames = []
fast_total_incorrect_token_frames = []

# Create dataframe with index every framerate (10ms) and initialize with silence
for filename in list_of_filenames:
    correct_empty_frames = 0
    correct_token_frames = 0
    incorrect_token_frames = 0
    number_of_frames = 0

    df_current_gold_ctm = gold_ctms_df.loc[gold_ctms_df['Filename'] == filename][["start", "end", "token"]]
    df_current_created_ctm = created_ctms_df.loc[created_ctms_df['Filename'] == filename][["start", "end", "token"]]

    endtime = int(max(df_current_gold_ctm["end"].max(), df_current_created_ctm["end"].max()) * 1000)  # in ms
    framerate = 10  # in ms
    frames_current_gold_ctm = ["!SIL" for x in range(0, endtime + framerate, framerate)]
    frames_current_created_ctm = ["!SIL" for x in range(0, endtime + framerate, framerate)]

    # Go over the ctm token by token and use times to put that token into those indexes
    for frames_and_ctm in [[df_current_gold_ctm, frames_current_gold_ctm],
                           [df_current_created_ctm, frames_current_created_ctm]]:
        for ctm_row in frames_and_ctm[0].itertuples():

            # Calculate the indexes with framerate
            tokens_start_index = int(ctm_row.start * 1000 / framerate)
            tokens_end_index = int(ctm_row.end * 1000 / framerate)
            for index in range(tokens_start_index, tokens_end_index + 1):
                frames_and_ctm[1][index] = ctm_row.token

    for gold_frame, created_frame in zip(frames_current_gold_ctm, frames_current_created_ctm):
        if gold_frame == "!SIL" and created_frame == "!SIL":
            correct_empty_frames += 1
        elif gold_frame == created_frame:
            correct_token_frames += 1
        else:
            incorrect_token_frames += 1

    fast_total_correct_token_frames.append(correct_token_frames)
    fast_total_correct_empty_frames.append(correct_empty_frames)
    fast_total_incorrect_token_frames.append(incorrect_token_frames)

ctm_mistakes_seconds = []
for filename in list_of_filenames:

    df_current_gold_ctm = gold_ctms_df.loc[gold_ctms_df['Filename'] == filename][["start", "end", "token"]]
    df_current_created_ctm = created_ctms_df.loc[created_ctms_df['Filename'] == filename][["start", "end", "token"]]

    # ["OP", "REF", "HYP"]
    # "OK","SUB","INS", "***", "DEL", "***"
    wer_results, token_comparisons = \
        wer(df_current_gold_ctm["token"].tolist(), df_current_created_ctm["token"].tolist(), True)

    # Iterate three things
    gold_iterator = df_current_gold_ctm.itertuples()
    created_iterator = df_current_created_ctm.itertuples()
    for comparison_row in token_comparisons[1:]:
        if comparison_row[0] == "OK" or comparison_row[0] == "SUB":

            gold_ctm_row = next(gold_iterator)
            created_ctm_row = next(created_iterator)

            start_difference = created_ctm_row.start - gold_ctm_row.start
            end_difference = created_ctm_row.end - gold_ctm_row.end
            ctm_mistakes_seconds.append([start_difference, end_difference])

        elif comparison_row[0] == "INS":
            created_ctm_row = next(created_iterator)
        elif comparison_row[0] == "DEL":
            gold_ctm_row = next(gold_iterator)
        else:
            print("Something went terribly wrong")
            break

# STATISTICS
fast_correct_empty_np_arr = np.asarray(fast_total_correct_empty_frames)
fast_correct_token_np_arr = np.asarray(fast_total_correct_token_frames)
fast_incorrect_np_arr = np.asarray(fast_total_incorrect_token_frames)

difference_np_arr = np.asarray(ctm_mistakes_seconds)

# Median start difference
start_difference_median = np.median(difference_np_arr[:, 0])

# What percentage of tokens are inside 40ms of actual start
percentileofscore_of_40ms_start = percentileofscore(np.abs(difference_np_arr[:, 0]), 0.04)

# What percentage of tokens are inside 40ms of actual end
percentileofscore_of_40ms_end = percentileofscore(np.abs(difference_np_arr[:, 1]), 0.04)

fig1, (ax1, ax2, ax3) = plt.subplots(1, 3, sharey=True)
ax1.set_title('Token')
ax2.set_title('Empty')
ax3.set_title('Wrong')
ax1.boxplot(fast_correct_token_np_arr)
ax2.boxplot(fast_correct_empty_np_arr)
ax3.boxplot(fast_incorrect_np_arr)
plt.savefig('testibox.png', bbox_inches='tight')
plt.clf()

plt.hist(difference_np_arr[:, 0], 100, facecolor='g', alpha=0.75)
plt.xlabel('Start difference')
plt.ylabel('#Tokens')
plt.title('Histogram of start differences')
plt.xlim(-0.3, 0.3)
plt.grid(True)
plt.savefig('testihist.png', bbox_inches='tight')

plt.hist(difference_np_arr[:, 1], 100, facecolor='g', alpha=0.75)
plt.xlabel('End difference')
plt.ylabel('#Tokens')
plt.title('Histogram of end differences')
plt.xlim(-0.3, 0.3)
plt.grid(True)
plt.savefig('testihist2.png', bbox_inches='tight')

print(percentileofscore_of_40ms_start, percentileofscore_of_40ms_end)