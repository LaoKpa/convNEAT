import itertools
import time
import os
import pickle
import math
import random
import logging
import numpy as np
from matplotlib.patches import Polygon
from matplotlib.collections import PatchCollection

from KMedoids import KMedoids
from genome import Genome
from net import build_net_from_genome


class Population:
    """
    A population of genomes to be evolved
    -----
    n                - population size
    evaluate         - how to get a score from genome
    parent_selection - how parents are selected from the population
    crossover        - how to combine genomes to form new ones
    train            - how to train net nets
    epochs           - the standard (minimum) number of epochs to train before evaluation
    min_species_size - the lower limit to species sizes (cur. only used for spawn calculations)
    elitism_rate     - the % of best genomes to transfer ot the new generation
    load = [checkpoint_name, generation] - whether to load from a checkpoint
    monitor          - if the results should be shown graphically
    """

    def __init__(self, n, evaluate, parent_selection, crossover, train, input_size, output_size,
                 name=None, elitism_rate=0.1, min_species_size=5, epochs=2, load=None, monitor=None):
        # Evolution parameters
        self.evaluate = evaluate
        self.parent_selection = parent_selection
        self.crossover = crossover
        self.train = train
        self.epochs = epochs
        self.min_species_size = min_species_size
        self.elitism_rate = elitism_rate

        # Plotting and tracking training progress
        self.monitor = monitor

        # Load instead
        if load is not None:
            self.load_checkpoint(*load)
        else:
            # Historical markers starting at 5
            self.id_generator = itertools.count(5)
            self.species_id_generator = itertools.count(1)

            self.input_size = input_size
            self.output_size = output_size

            # Begin with only one species
            self.n = n
            self.number_of_species = 1
            self.species = {0: [Genome(self) for _ in range(n)]}
            self.best_genome = self.species[0][0].copy()
            self.top_score = 0
            self.history = []

            self.generation = 1
            self.checkpoint_name = name or time.strftime("%d.%m-%H:%M")

    def next_id(self):
        return next(self.id_generator)

    def save_checkpoint(self):
        save = [self.n, self.id_generator, self.species_id_generator, self.number_of_species, self.generation,
                self.input_size, self.output_size, self.checkpoint_name, self.top_score, self.history,
                (self.best_genome.__class__, self.best_genome.save()),
                {species: [(genome.__class__, genome.save()) for genome in genomes]
                 for species, genomes in self.species.items()}]

        _dir = os.path.join('checkpoints', self.checkpoint_name)
        if not os.path.exists(_dir):
            os.makedirs(_dir)

        with open(os.path.join(_dir, "%02d.cp" % self.generation), "wb") as c:
            pickle.dump(save, c)

    def load_checkpoint(self, checkpoint_name, generation):
        file_path = os.path.join('checkpoints', checkpoint_name, "%02d.cp" % generation)
        with open(file_path, "rb") as c:
            [self.n, self.id_generator, self.species_id_generator, self.number_of_species, self.generation,
             self.input_size, self.output_size, self.checkpoint_name, self.top_score, self.history,
             saved_best_genome, saved_genomes] = pickle.load(c)
            self.best_genome = saved_best_genome[0](self).load(saved_best_genome[1])
            self.species = {species: [genome[0](self).load(genome[1]) for genome in genomes]
                            for species, genomes in saved_genomes.items()}

    def cluster(self, relative=False):
        """
        Cluster the genomes with K_Medoids-Clustering
        Change the number of species if needed (-2 .. +2)

        # absolute clustering
        The number of clusters is the minimum needed to achieve a score of <1500

        # relative clustering
        The number of cluster decreases if the score of k-1 is higher by <20%
        The number of cluster decreases if the score of k+1 is smaller by >50%
        """
        k = self.number_of_species
        n = self.n

        # Sorted after species size
        sorted_species_ids = sorted(self.species.keys(), key=lambda i: len(self.species.get(i)))
        all_genomes = [g for i in sorted_species_ids for g in self.species[i]]

        # Distance matrix
        distances = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                distances[i, j] = all_genomes[i].dissimilarity(all_genomes[j])

        # Get centers of old species
        species_len = [len(self.species[i]) for i in sorted_species_ids]
        cumlen = np.cumsum([0] + species_len)
        cur_centers = []
        labels = np.array([i for i in sorted_species_ids for _ in self.species[i]])
        for i in range(k):
            in_cluster_distances = distances[np.ix_(labels == i, labels == i)]
            cur_centers += [np.argmin(np.sum(in_cluster_distances, axis=1)) + cumlen[i]]

        # Get performance of K-Medoids for some # of clusters near k
        ids_to_check = list(range(max(1, k - 2), min(int(n / self.min_species_size) + 1, k + 3)))
        medoids = {i: KMedoids(n_clusters=i, metric='precomputed').fit(distances, old_centers=cur_centers)
                   for i in ids_to_check}
        all_labels = {i: medoid.labels_ for i, medoid in medoids.items()}
        scores = {i: medoid.score_ for i, medoid in medoids.items()}

        # Clustering performance
        logging.info("Scores for  clustering:\n" +
                     ", ".join(["%s: %s" % (sp, sc) for sp, sc in zip(ids_to_check, list(scores.values()))]))

        if relative:
            # Change number of clusters
            while k + 1 in ids_to_check and scores[k + 1] < 0.5 * scores[k]:
                print("number of clusters increased by one")
                k += 1
                sorted_species_ids += [next(self.species_id_generator)]
            while k - 1 in ids_to_check and scores[k - 1] < 1.20 * scores[k]:
                print("number of clusters increased by one")
                k -= 1
        else:
            while k + 1 in ids_to_check and scores[k] >= 1500:
                print("number of clusters increased by one")
                k += 1
                sorted_species_ids += [next(self.species_id_generator)]
            while k - 1 in ids_to_check and scores[k - 1] < 1500:
                print("number of clusters increased by one")
                k -= 1

        # Save new Clustering
        self.number_of_species = k
        # Use old identifiers for clusters
        labels = [sorted_species_ids[i] for i in all_labels[k]]

        # Rebuild species
        self.species = {i: [] for i in sorted_species_ids if i in labels}
        for i, g in enumerate(all_genomes):
            self.species[labels[i]] += [g]

        # Save to history starting with newest
        self.history += [{species: [len(genomes), None]
                          for species, genomes in sorted(self.species.items(), key=lambda x: x[0], reverse=True)}]

        if self.monitor is not None:
            # Plot distance matrix after clustering with cluster boxes
            species_len = np.cumsum([0] + [len(g) for g in self.species.values()])
            ind = np.argsort(labels)
            self.monitor.plot(3, distances[np.ix_(ind, ind)], kind='imshow', clear=True)
            for s, e in zip(species_len[:-1], species_len[1:]):
                self.monitor.plot(3, np.array([s, s, e, e, s]) - 0.5, np.array([s, e, e, s, s]) - 0.5,
                                  c='red', linewidth=1.5)
            self.monitor.send()

            # Plot Species over Generation plot
            self.monitor.plot(2, clear=True)
            scores = [np.array([sc for _, sc in hist.values()]) for hist in self.history[:-1]]
            lens = [[len_ for len_, _ in hist.values()] for hist in self.history]
            cumlens = [np.cumsum([0] + l) for l in lens]
            sp_ids = [hist.keys() for hist in self.history]
            pos = [{sp: pos for sp, pos in zip(sp_id, cumlen)} for sp_id, cumlen in zip(sp_ids, cumlens)]
            if len(pos) == 1:
                pos = [{0: 100}] + pos
                sp_ids = [pos[0].keys()] + sp_ids
            print(pos, sp_ids)
            for i in range(len(pos) - 1):
                connections = [[pos[i][sp], pos[i+1][sp]] for sp in sp_ids[i] & sp_ids[i+1]]
                new_sp = [[pos[i][max([p for p in sp_ids[i] if p < sp])] if min(sp_ids[i]) < sp else max(cumlens[i]),
                           pos[i+1][sp]]
                          for sp in sp_ids[i+1] - sp_ids[i]]
                dead_sp = [[pos[i][sp], pos[i+1][max([p for p in sp_ids[i+1] if p < sp])]
                            if min(sp_ids[i+1]) < sp else self.n]
                           for sp in sp_ids[i] - sp_ids[i+1]]
                lines = [[self.n, self.n]] + connections + new_sp + dead_sp
                # Plot scores
                if i < len(scores):
                    patches = []
                    for sp in sp_ids[i+1]:
                        base = [sp if sp in sp_ids[i] else max([p for p in sp_ids[i] if p < sp])
                                if min(sp_ids[i]) < sp else self.n, sp]
                        bel = [pos[i][base[0]], pos[i+1][base[1]]]
                        abv = list(map(lambda x: pos[x][max([p for p in sp_ids[x] if p < sp])]
                                       if min(sp_ids[x]) < sp else self.n, [i, i+1]))
                        polygon = Polygon([[i, bel[0]], [i, abv[0]], [i+1, abv[1]], [i+1, bel[1]]], True)
                        patches.append(polygon)
                    p = PatchCollection(patches, cmap='viridis', alpha=0.4)
                    colors = scores[i]**3
                    p.set_array(np.array(colors))

                    self.monitor.plot(2, p, kind='add_collection')
                self.monitor.plot(2, [i, i+1], [[l for l, _ in lines], [l for _, l in lines]], c='darkblue')
            self.monitor.send()

    def new_species_sizes(self, score_by_species):
        """
        Calculate new the new sizes for every species (proportionate to fitness)
        """
        scores = np.array(list(score_by_species.values()))
        sizes = np.maximum(scores/sum(scores) * self.n, self.min_species_size)
        sizes = np.around(sizes/sum(sizes) * self.n)

        # Force n genomes
        while sum(sizes) > self.n:
            r = random.choice(list(np.where(sizes > self.min_species_size)))
            sizes[r] -= 1
        while sum(sizes) < self.n:
            sizes[random.choice(range(sizes.shape[0]))] += 1

        return {sp: int(size) for sp, size in zip(score_by_species.keys(), sizes)}

    def evolve(self):
        """
        Group the genomes to species and evaluate them on training data
        Generate the next generation with selection, crossover and mutation
        """
        # Saving checkpoint
        print("Saving checkpoint\n")
        self.save_checkpoint()

        # show best net
        if self.monitor is not None:
            self.monitor.plot(0, (self.best_genome.__class__, self.best_genome.save()), kind='net-plot',
                              input_size=self.input_size, score=self.top_score, title='best', clear=True, show=True)

        self.cluster()

        # Training nets
        counter = itertools.count(1)
        evaluated_genomes_by_species = dict()
        for sp, genomes in self.species.items():
            evaluated_genomes = []
            for g in genomes:
                i = next(counter)
                print('Instantiating neural network from the following genome in species %d - (%d/%d):' %
                      (sp, i, self.n))
                print(g)

                # Visualize current net
                if self.monitor is not None:
                    self.monitor.plot(1, (g.__class__, g.save()), kind='net-plot', title='train',
                                      n=self.n, i=i, input_size=self.input_size, clear=True, show=True)

                logging.debug('Building Net')
                net, optim, criterion = build_net_from_genome(g, self.input_size, self.output_size)
                self.train(g, net, optim, criterion, epochs=self.epochs)
                score = self.evaluate(net)
                g.score = score

                # Show best net
                if score > self.top_score:
                    self.top_score = score
                    self.best_genome = g.copy()
                    if self.monitor is not None:
                        self.monitor.plot(0, (g.__class__, g.save()), kind='net-plot',  title='best',
                                          input_size=(1, 28, 28), score=score, clear=True, show=True)

                evaluated_genomes += [(g, score)]
            print(evaluated_genomes)
            evaluated_genomes_by_species[sp] = sorted(evaluated_genomes, key=lambda x: x[1], reverse=True)

        # Saving checkpoint with net parameters
        print("Saving checkpoint after training\n")
        self.save_checkpoint()

        print('\n\nGENERATION %d\n' % self.generation)
        for species, evaluated_genomes in evaluated_genomes_by_species.items():
            print('Species %d with %d members:\n' % (species, len(evaluated_genomes)))
            for g, s in evaluated_genomes:
                r = repr(g)
                if len(r) > 64:
                    r = r[:60] + '...' + r[-1:]
                print('{:64}:'.format(r), s)
            print()

        # Score of species is mean of scores
        score_by_species = {species: sum([s for g, s in genomes]) / len(genomes)
                            for species, genomes in evaluated_genomes_by_species.items()}

        # Update History
        for sp, sc in score_by_species.items():
            self.history[-1][sp] = [self.history[-1][sp][0], score_by_species[sp]]

        # Resize species, increase better scoring species
        new_sizes = self.new_species_sizes(score_by_species)

        print("Breading new neural networks")
        # Same mutation (split_edge) in a gen get the same innovation number
        this_gen_mutations = dict()
        for sp, evaluated_genomes in evaluated_genomes_by_species.items():
            old_n_sp = len(evaluated_genomes)
            new_n_sp = new_sizes[sp]
            elitism = min(math.ceil(self.elitism_rate * old_n_sp), new_n_sp)
            elite_genomes = [g for g, s in evaluated_genomes[:elitism]]
            print("%d Elites in species %d:" % (elitism, sp))
            for g in elite_genomes:
                print(g)
            print()
            parents = self.parent_selection(evaluated_genomes, k=new_n_sp-elitism)
            new_genomes = [self.crossover(p[0], p[1]).mutate_random(this_gen_mutations) for p in parents]
            self.species[sp] = elite_genomes + new_genomes

        self.generation += 1
